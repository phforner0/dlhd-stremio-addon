from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import logging
import re
import sys
import threading
import uuid
import ctypes
from urllib.parse import urlparse

from app import settings

_CONFIGURE_GUARD = threading.Lock()
_CONFIGURED = False
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{12,}$")
_HEX_RE = re.compile(r"^[0-9a-fA-F]{8,}$")


def configure_logging() -> None:
    global _CONFIGURED

    level_name = (settings.LOG_LEVEL or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    text_format = "%(asctime)s %(levelname)s %(name)s %(message)s"
    formatter = logging.Formatter("%(message)s" if settings.LOG_FORMAT == "json" else text_format)

    with _CONFIGURE_GUARD:
        root = logging.getLogger()
        root.setLevel(level)

        if root.handlers:
            for handler in root.handlers:
                handler.setLevel(level)
                if handler.formatter is None:
                    handler.setFormatter(formatter)
            _CONFIGURED = True
            return

        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)
        handler.setFormatter(formatter)
        root.handlers.clear()
        root.addHandler(handler)
        _CONFIGURED = True


def hash_url(url: str | None) -> str | None:
    if not url:
        return None
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def _normalize_segment(segment: str) -> str:
    if not segment:
        return segment
    if segment.startswith("cfg-"):
        return ":config"

    stem, dot, suffix = segment.partition(".")
    normalized = stem
    if stem.isdigit():
        normalized = ":id"
    elif _HEX_RE.fullmatch(stem):
        normalized = ":hex"
    elif _TOKEN_RE.fullmatch(stem):
        normalized = ":token"

    return f"{normalized}{dot}{suffix}" if dot else normalized


def normalize_path_family(path: str | None) -> str | None:
    if not path:
        return None
    if path == "/":
        return "/"
    segments = [_normalize_segment(segment) for segment in path.split("/")]
    normalized = "/".join(segments)
    return normalized or "/"


def proxy_url_fields(url: str | None) -> dict[str, str | None]:
    parsed = urlparse(url or "")
    return {
        "upstream_host": parsed.hostname,
        "upstream_path": normalize_path_family(parsed.path),
        "upstream_url_hash": hash_url(url),
    }


def config_fingerprint(config: Mapping[str, object] | None) -> str | None:
    if not config:
        return None
    raw = json.dumps(dict(sorted(config.items())), separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:12]


def request_id_from_request(request) -> str:
    state_request_id = getattr(request.state, "request_id", None)
    if state_request_id:
        return state_request_id

    header_request_id = request.headers.get("x-request-id") or request.headers.get("x-correlation-id")
    if header_request_id:
        request_id = header_request_id.strip()[:64]
    else:
        request_id = uuid.uuid4().hex[:12]

    request.state.request_id = request_id
    return request_id


def current_rss_mb() -> float | None:
    try:
        import resource  # type: ignore[attr-defined]

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == "darwin":
            return round(usage / (1024 * 1024), 1)
        return round(usage / 1024, 1)
    except Exception:
        pass

    if sys.platform != "win32":
        return None

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
    kernel32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return None
    return round(counters.WorkingSetSize / (1024 * 1024), 1)


def _coerce_field(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return {str(key): _coerce_field(val) for key, val in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_coerce_field(item) for item in value]
    return str(value)


def _text_value(value: object) -> str:
    coerced = _coerce_field(value)
    return json.dumps(coerced, ensure_ascii=True, separators=(",", ":"))


def log_event(logger: logging.Logger, level: int, event: str, **fields: object) -> None:
    payload = {"event": event}
    for key, value in fields.items():
        if value is not None:
            payload[key] = _coerce_field(value)

    if settings.LOG_FORMAT == "json":
        logger.log(level, json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
        return

    message = " ".join([f"event={event}", *[f"{key}={_text_value(value)}" for key, value in payload.items() if key != "event"]])
    logger.log(level, message)
