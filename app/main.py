from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import ipaddress
import json
import logging
import re
import threading
import socket
from time import perf_counter
from urllib.parse import quote, unquote, urlencode, urljoin, urlparse

import requests

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.responses import StreamingResponse

from app import settings
from app.cache import TTLCache
from app.http import DEFAULT_HEADERS, build_session, close_pooled_sessions, get_pooled_session, pooled_session_count
from app.logging_utils import (
    config_fingerprint,
    configure_logging,
    current_rss_mb,
    log_event,
    normalize_path_family,
    proxy_url_fields,
    request_id_from_request,
)
from app.manifest import build_manifest
from app.models import CatalogChannel, LiveEvent, WrapperCatalog
from app.posters import render_svg_poster
from app.resolve.player import PlaywrightResolver
from app.scrape.channels import filter_channels, scrape_channels
from app.scrape.schedule import _display_schedule_values, filter_schedule, scrape_schedule
from app.scrape.watch import WatchFetchError, fetch_wrapper
from app.user_config import (
    catalog_mode as user_catalog_mode,
    channel_stream_result_limit as user_channel_stream_result_limit,
    configure_select_fields,
    encode_user_config,
    event_stale_after_minutes as user_event_stale_after_minutes,
    live_stream_result_limit as user_live_stream_result_limit,
    parse_user_config as parse_user_config_input,
    preferred_country_code as user_preferred_country_code,
    schedule_offset_minutes as user_schedule_offset_minutes,
)

configure_logging()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _log_app_start()
    _log_resource_snapshot("startup")
    _start_resource_logger()
    try:
        yield
    finally:
        _stop_resource_logger()
        _log_app_shutdown()
        resolver.close()
        close_pooled_sessions()
        _log_resource_snapshot("shutdown")


app = FastAPI(title=settings.ADDON_NAME, docs_url=None, redoc_url=None, lifespan=lifespan)
LOGGER = logging.getLogger("dlhd.addon")
HLS_URI_ATTR_RE = re.compile(r'URI="([^"]+)"')

channels_cache: TTLCache[list[CatalogChannel]] = TTLCache(max_entries=settings.CHANNELS_CACHE_MAX_ENTRIES, name="channels")
channel_index_cache: TTLCache[dict[int, CatalogChannel]] = TTLCache(
    max_entries=settings.CHANNEL_INDEX_CACHE_MAX_ENTRIES,
    name="channel_index",
)
schedule_cache: TTLCache[list[LiveEvent]] = TTLCache(max_entries=settings.SCHEDULE_CACHE_MAX_ENTRIES, name="schedule")
schedule_index_cache: TTLCache[dict[str, LiveEvent]] = TTLCache(
    max_entries=settings.SCHEDULE_INDEX_CACHE_MAX_ENTRIES,
    name="schedule_index",
)
watch_cache: TTLCache[WrapperCatalog] = TTLCache(max_entries=settings.WATCH_CACHE_MAX_ENTRIES, name="watch")
stream_cache: TTLCache[list[dict]] = TTLCache(max_entries=settings.STREAM_CACHE_MAX_ENTRIES, name="stream")
live_channel_stream_cache: TTLCache[dict[str, str]] = TTLCache(
    max_entries=settings.LIVE_CHANNEL_CACHE_MAX_ENTRIES,
    name="live_channel",
)
playlist_cache: TTLCache[str] = TTLCache(max_entries=settings.HLS_PLAYLIST_CACHE_MAX_ENTRIES, name="playlist")
manifest_validation_cache: TTLCache[bool] = TTLCache(
    max_entries=settings.HLS_VALIDATION_MAX_ENTRIES,
    name="manifest_validation",
)
manifest_validation_reason_cache: TTLCache[str] = TTLCache(
    max_entries=settings.HLS_VALIDATION_MAX_ENTRIES,
    name="manifest_validation_reason",
)
resolver = PlaywrightResolver()
RESOURCE_LOG_STOP = threading.Event()
RESOURCE_LOG_THREAD: threading.Thread | None = None
RESOURCE_THRESHOLD_EMITTED = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "HEAD", "OPTIONS"],
    allow_headers=["*"],
    max_age=86400,
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = request_id_from_request(request)
    started_at = perf_counter()
    if settings.LOG_REQUEST_START:
        log_event(LOGGER, logging.INFO, "request_start", **_request_log_fields(request))

    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-Id"] = request_id
        return response
    except HTTPException as exc:
        status_code = exc.status_code
        raise
    finally:
        log_event(
            LOGGER,
            logging.INFO,
            "request_end",
            **_request_log_fields(
                request,
                status_code=status_code,
                duration_ms=round((perf_counter() - started_at) * 1000),
            ),
        )


def _log_timing(message: str, started_at: float, **fields: object) -> None:
    elapsed_ms = round((perf_counter() - started_at) * 1000)
    event = message.strip().lower().replace(" ", "_")
    log_event(LOGGER, logging.DEBUG, event, duration_ms=elapsed_ms, **fields)


def _route_family(request: Request) -> str:
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    if isinstance(route_path, str):
        return route_path
    return normalize_path_family(request.url.path) or request.url.path


def _request_log_fields(request: Request, **extra: object) -> dict[str, object]:
    return {
        "request_id": request_id_from_request(request),
        "route": _route_family(request),
        "method": request.method,
        **extra,
    }


def _cache_counts() -> dict[str, int]:
    return {
        "channels_entries": channels_cache.entry_count(),
        "schedule_entries": schedule_cache.entry_count(),
        "watch_entries": watch_cache.entry_count(),
        "stream_entries": stream_cache.entry_count(),
        "live_channel_entries": live_channel_stream_cache.entry_count(),
        "playlist_entries": playlist_cache.entry_count(),
        "manifest_validation_entries": manifest_validation_cache.entry_count(),
    }


def _resource_snapshot_fields() -> dict[str, object]:
    return {
        "rss_mb": current_rss_mb(),
        "active_browsers": resolver.active_browser_count(),
        "pooled_http_sessions": pooled_session_count(),
        **_cache_counts(),
    }


def _log_resource_snapshot(reason: str, *, level: int = logging.INFO) -> None:
    global RESOURCE_THRESHOLD_EMITTED

    fields = _resource_snapshot_fields()
    rss_mb = fields.get("rss_mb")
    log_event(LOGGER, level, "resource_snapshot", reason=reason, **fields)

    if isinstance(rss_mb, (int, float)) and rss_mb >= settings.RESOURCE_LOG_RSS_MB_THRESHOLD:
        if not RESOURCE_THRESHOLD_EMITTED:
            RESOURCE_THRESHOLD_EMITTED = True
            log_event(LOGGER, logging.WARNING, "resource_snapshot", reason="rss_threshold_crossed", **fields)
    else:
        RESOURCE_THRESHOLD_EMITTED = False


def _resource_logger_loop() -> None:
    interval = max(settings.RESOURCE_LOG_INTERVAL_SECONDS, 1)
    while not RESOURCE_LOG_STOP.wait(interval):
        _log_resource_snapshot("interval", level=logging.DEBUG)


def _start_resource_logger() -> None:
    global RESOURCE_LOG_THREAD

    RESOURCE_LOG_STOP.clear()
    if not settings.RESOURCE_LOG_ENABLED or settings.RESOURCE_LOG_INTERVAL_SECONDS <= 0:
        return

    RESOURCE_LOG_THREAD = threading.Thread(target=_resource_logger_loop, name="resource-logger", daemon=True)
    RESOURCE_LOG_THREAD.start()


def _stop_resource_logger() -> None:
    RESOURCE_LOG_STOP.set()
    if RESOURCE_LOG_THREAD is not None:
        RESOURCE_LOG_THREAD.join(timeout=1)


def _log_app_start() -> None:
    log_event(
        LOGGER,
        logging.INFO,
        "app_start",
        version=settings.ADDON_VERSION,
        playwright_max_concurrency=settings.PLAYWRIGHT_MAX_CONCURRENCY,
        playwright_reuse_browser=settings.PLAYWRIGHT_REUSE_BROWSER,
        channel_stream_max_attempts=settings.CHANNEL_STREAM_MAX_ATTEMPTS,
        live_stream_max_attempts=settings.LIVE_STREAM_MAX_ATTEMPTS,
        live_stream_budget_seconds=settings.LIVE_STREAM_BUDGET_SECONDS,
        proxy_allowed_hosts=list(settings.PROXY_ALLOWED_HOSTS),
        proxy_max_redirects=settings.PROXY_MAX_REDIRECTS,
        http_pool_connections=settings.HTTP_POOL_CONNECTIONS,
        http_pool_maxsize=settings.HTTP_POOL_MAXSIZE,
        safe_http_retry_total=settings.SAFE_HTTP_RETRY_TOTAL,
        safe_http_retry_backoff_seconds=settings.SAFE_HTTP_RETRY_BACKOFF_SECONDS,
        tls_ignore_global=settings.PLAYWRIGHT_IGNORE_HTTPS_ERRORS,
        tls_ignore_hosts=list(settings.PLAYWRIGHT_IGNORE_HTTPS_ERROR_HOSTS),
        channels_cache_max_entries=settings.CHANNELS_CACHE_MAX_ENTRIES,
        schedule_cache_max_entries=settings.SCHEDULE_CACHE_MAX_ENTRIES,
        watch_cache_max_entries=settings.WATCH_CACHE_MAX_ENTRIES,
        stream_cache_max_entries=settings.STREAM_CACHE_MAX_ENTRIES,
        live_channel_cache_max_entries=settings.LIVE_CHANNEL_CACHE_MAX_ENTRIES,
        playlist_cache_max_entries=settings.HLS_PLAYLIST_CACHE_MAX_ENTRIES,
        hls_validation_cache_max_entries=settings.HLS_VALIDATION_MAX_ENTRIES,
    )


def _log_app_shutdown() -> None:
    log_event(LOGGER, logging.INFO, "app_shutdown", **_resource_snapshot_fields())


def _service_base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _playlist_cache_key(base_url: str, url: str, referer: str) -> str:
    return f"playlist:{base_url}|{url}|{referer}"


def _hls_validation_cache_key(manifest_url: str, referer: str) -> str:
    return f"hls-valid:{manifest_url}|{referer}"


def _host_allowed(host: str) -> bool:
    lowered = host.lower()
    for allowed in settings.PROXY_ALLOWED_HOSTS:
        if allowed.startswith("."):
            if lowered == allowed[1:] or lowered.endswith(allowed):
                return True
        elif lowered == allowed:
            return True
    return False


def _public_host(host: str) -> bool:
    try:
        resolved = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False

    for entry in resolved:
        ip = entry[4][0]
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_multicast
            or addr.is_reserved
            or addr.is_unspecified
        ):
            return False
    return True


def _proxy_url_error(raw_url: str) -> tuple[int | None, str | None]:
    parsed = urlparse(raw_url)
    if parsed.scheme not in {"http", "https"}:
        return 400, "invalid_scheme"
    if not parsed.hostname:
        return 400, "invalid_host"
    if not _host_allowed(parsed.hostname):
        return 403, "host_not_allowlisted"
    if not _public_host(parsed.hostname):
        return 403, "private_ip_blocked"
    return None, None


def _validate_proxy_url(raw_url: str, field_name: str) -> str:
    status_code, reason = _proxy_url_error(raw_url)
    if reason == "invalid_scheme":
        raise HTTPException(status_code=400, detail=f"invalid {field_name} scheme")
    if reason == "invalid_host":
        raise HTTPException(status_code=400, detail=f"invalid {field_name} host")
    if reason == "host_not_allowlisted":
        raise HTTPException(status_code=403, detail=f"disallowed {field_name} host")
    if reason == "private_ip_blocked":
        raise HTTPException(status_code=403, detail=f"disallowed {field_name} address")
    if status_code is not None:
        raise HTTPException(status_code=status_code, detail=f"invalid {field_name}")
    return raw_url


def _proxy_redirect_decision(current_url: str, target_url: str) -> tuple[bool, str | None, str | None]:
    current = urlparse(current_url)
    target = urlparse(target_url)
    if not target.hostname or target.scheme not in {"http", "https"}:
        return False, "redirect_host_blocked", None
    if not _public_host(target.hostname):
        return False, "redirect_private_ip", None
    if _host_allowed(target.hostname):
        return True, None, "allowlisted_host"

    if current.hostname and _host_allowed(current.hostname) and current.path.startswith("/redirect/media/"):
        if target.path.startswith("/media/"):
            return True, None, "media_chain"
        return False, "redirect_host_blocked", "media_chain"

    return False, "redirect_host_blocked", None


def _parse_skip(raw_skip: str | None) -> int:
    if raw_skip is None or raw_skip == "":
        return 0
    try:
        parsed = int(raw_skip)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid skip") from exc
    return max(parsed, 0)


def _wrapper_or_http_error(channel_id: int) -> WrapperCatalog:
    try:
        return get_wrapper(channel_id)
    except WatchFetchError as exc:
        if exc.status_code == 404:
            raise HTTPException(status_code=404, detail=f"channel {channel_id} not found") from exc
        raise HTTPException(status_code=502, detail=f"failed to fetch channel {channel_id}") from exc


def _looks_like_hls_playlist(body: str) -> bool:
    return body.lstrip().startswith("#EXTM3U")


def _extract_hls_probe_targets(body: str, base_url: str) -> tuple[str | None, str | None]:
    key_url = None
    media_url = None
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#EXT-X-KEY") and key_url is None:
            match = HLS_URI_ATTR_RE.search(stripped)
            if match:
                key_url = urljoin(base_url, match.group(1))
            continue
        if stripped.startswith("#"):
            continue
        media_url = urljoin(base_url, stripped)
        break
    return key_url, media_url


def _is_hls_playlist_candidate(url: str, content_type: str) -> bool:
    lowered_path = urlparse(url).path.lower()
    lowered_type = content_type.lower()
    return (
        lowered_path.endswith(".m3u8")
        or lowered_path.endswith(".css")
        or "mpegurl" in lowered_type
        or lowered_type in {"text/plain", "text/txt", "application/vnd.apple.mpegurl"}
    )


def _hls_validation_reason(manifest_url: str, referer: str) -> str | None:
    return manifest_validation_reason_cache.get(_hls_validation_cache_key(manifest_url, referer), allow_stale=True)


def _probe_hls_target(url: str, referer: str, *, depth: int = 0) -> tuple[bool, str | None]:
    if depth > 2:
        log_event(LOGGER, logging.DEBUG, "hls_validation_fail", reason="media_missing", depth=depth, **proxy_url_fields(url))
        return False, "media_missing"

    session = build_session()
    try:
        headers = {"Referer": referer}
        log_event(LOGGER, logging.DEBUG, "hls_validation_start", depth=depth, **proxy_url_fields(url))
        response = _follow_proxy_redirects(session, url, headers)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()

        if content_type in {"application/json", "text/html"}:
            response.close()
            reason = "media_html_instead_of_binary" if content_type == "text/html" else "playlist_candidate_not_hls"
            log_event(LOGGER, logging.DEBUG, "hls_validation_fail", reason=reason, depth=depth, **proxy_url_fields(response.url))
            return False, reason

        if _is_hls_playlist_candidate(response.url, content_type):
            body = response.text
            response.close()
            if not _looks_like_hls_playlist(body):
                log_event(LOGGER, logging.DEBUG, "hls_validation_fail", reason="not_extm3u", depth=depth, **proxy_url_fields(url))
                return False, "not_extm3u"

            key_url, media_url = _extract_hls_probe_targets(body, response.url)
            if key_url is not None:
                try:
                    key_response = _follow_proxy_redirects(session, key_url, headers)
                    key_response.raise_for_status()
                    key_response.close()
                except HTTPException:
                    log_event(LOGGER, logging.DEBUG, "hls_validation_fail", reason="media_redirect_blocked", depth=depth, **proxy_url_fields(key_url))
                    return False, "media_redirect_blocked"
                except requests.HTTPError:
                    log_event(LOGGER, logging.DEBUG, "hls_validation_fail", reason="key_fetch_failed", depth=depth, **proxy_url_fields(key_url))
                    return False, "key_fetch_failed"
            if media_url is None:
                log_event(LOGGER, logging.DEBUG, "hls_validation_fail", reason="media_missing", depth=depth, **proxy_url_fields(response.url))
                return False, "media_missing"
            return _probe_hls_target(media_url, referer, depth=depth + 1)

        response.close()
        log_event(LOGGER, logging.DEBUG, "hls_validation_pass", depth=depth, **proxy_url_fields(url))
        return True, None
    except HTTPException:
        log_event(LOGGER, logging.DEBUG, "hls_validation_fail", reason="media_redirect_blocked", depth=depth, **proxy_url_fields(url))
        return False, "media_redirect_blocked"
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        if status_code == 403:
            reason = "media_http_403"
        elif status_code == 404:
            reason = "media_http_404"
        else:
            reason = "validation_exception"
        log_event(LOGGER, logging.DEBUG, "hls_validation_fail", reason=reason, status_code=status_code, depth=depth, **proxy_url_fields(url))
        return False, reason
    except Exception as exc:  # noqa: BLE001
        log_event(
            LOGGER,
            logging.DEBUG,
            "hls_validation_fail",
            reason="validation_exception",
            error=exc.__class__.__name__,
            depth=depth,
            **proxy_url_fields(url),
        )
        return False, "validation_exception"
    finally:
        session.close()


def _valid_hls_stream(manifest_url: str, referer: str) -> bool:
    cache_key = _hls_validation_cache_key(manifest_url, referer)
    cached = manifest_validation_cache.get(cache_key)
    if cached is not None:
        return cached

    valid, reason = _probe_hls_target(manifest_url, referer)
    manifest_validation_cache.set(cache_key, valid, settings.HLS_VALIDATION_TTL_SECONDS)
    if reason:
        manifest_validation_reason_cache.set(cache_key, reason, settings.HLS_VALIDATION_TTL_SECONDS)
    else:
        manifest_validation_reason_cache.delete(cache_key)
    return valid


def _poster_url(request: Request, meta_id: str) -> str:
    return f"{_service_base_url(request)}/assets/poster/{quote(meta_id, safe='')}.svg"


def _parse_extra(extra: str | None) -> dict[str, str]:
    parsed: dict[str, str] = {}
    if not extra:
        return parsed
    for segment in extra.split("/"):
        if not segment or "=" not in segment:
            continue
        key, value = segment.split("=", 1)
        parsed[key] = unquote(value)
    return parsed


def _parse_user_config(config: str | None) -> dict[str, str]:
    parsed, _ = parse_user_config_input(config)
    return parsed


def _parse_user_config_for_request(request: Request, config: str | None) -> dict[str, str]:
    parsed, issues = parse_user_config_input(config)
    if config and issues:
        log_event(
            LOGGER,
            logging.INFO,
            "invalid_user_config",
            **_request_log_fields(request),
            config_issues=issues,
            user_config_hash=config_fingerprint(parsed),
        )
    return parsed


def _decode_user_config_token(config: str) -> dict[str, str]:
    parsed, _ = parse_user_config_input(config)
    return parsed


def _encode_user_config(config: dict[str, str]) -> str:
    return encode_user_config(config)


def _schedule_offset_minutes(config: dict[str, str]) -> int:
    return user_schedule_offset_minutes(config)


def _catalog_mode(config: dict[str, str]) -> str:
    return user_catalog_mode(config)


def _preferred_country_code(config: dict[str, str]) -> str | None:
    return user_preferred_country_code(config)


def _channel_stream_result_limit(config: dict[str, str]) -> int:
    return user_channel_stream_result_limit(config)


def _live_stream_result_limit(config: dict[str, str]) -> int:
    return user_live_stream_result_limit(config)


def _event_stale_after_minutes(config: dict[str, str]) -> int:
    return user_event_stale_after_minutes(config)


def _config_fingerprint(config: dict[str, str], keys: tuple[str, ...]) -> str:
    parts = []
    for key in keys:
        if key in config:
            parts.append(f"{key}={config[key]}")
    return "|".join(parts) or "default"


def _event_display_values(event: LiveEvent, config: dict[str, str]) -> tuple[str, str]:
    if event.scheduled_at_utc is None:
        return event.day_label, event.time_text
    return _display_schedule_values(event.day_label, event.time_text, event.scheduled_at_utc, _schedule_offset_minutes(config))


def _configure_page(request: Request) -> str:
    return _configure_page_v2(request)




def _configure_page_v2(request: Request) -> str:
    def select_html(field_id: str, title: str, options: list[tuple[str, str]], default_value: str) -> str:
        rendered = []
        for value, label in options:
            selected = " selected" if value == default_value else ""
            rendered.append(f'<option value="{value}"{selected}>{label}</option>')
        return f'<label for="{field_id}">{title}</label><select id="{field_id}">{"".join(rendered)}</select>'

    field_defs = configure_select_fields()
    field_ids = json.dumps([field["key"] for field in field_defs])

    fields_html = "".join(
        [
            select_html(field["key"], field["title"], field["options"], field["default"])
            for field in field_defs
        ]
    )

    base_url = str(request.base_url).rstrip("/")
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>{settings.ADDON_NAME} Configure</title>
  <style>
    body {{ font-family: Arial, sans-serif; max-width: 560px; margin: 40px auto; padding: 0 16px; background: #111827; color: #fff; }}
    h1 {{ margin-bottom: 8px; }}
    p {{ color: #d1d5db; line-height: 1.5; }}
    label {{ display: block; margin: 18px 0 8px; font-weight: 700; }}
    select, button {{ width: 100%; padding: 12px; font-size: 16px; border-radius: 8px; border: 1px solid #374151; }}
    select {{ background: #0f172a; color: #fff; }}
    button {{ margin-top: 20px; background: #8b5cf6; color: #fff; cursor: pointer; border: 0; }}
    button.secondary {{ background: #1f2937; }}
    a {{ color: #93c5fd; }}
    code {{ display: block; margin-top: 12px; padding: 12px; background: #0f172a; border-radius: 8px; color: #e5e7eb; word-break: break-all; }}
    .help {{ margin-top: 12px; font-size: 14px; }}
  </style>
</head>
<body>
  <h1>{settings.ADDON_NAME}</h1>
  <p>Choose what appears and how the addon behaves for this installation. Each user can install with different settings.</p>
  {fields_html}
  <button id=\"installBtn\">Install in Stremio</button>
  <button id=\"copyBtn\" class=\"secondary\" type=\"button\">Copy manifest URL</button>
  <p class=\"help\">If the install button opens a page-not-found screen, copy the manifest URL below and paste it manually inside Stremio.</p>
  <code id=\"manifestUrl\"></code>
  <script>
    const fields = {field_ids}
      .map((id) => document.getElementById(id));
    const installBtn = document.getElementById('installBtn');
    const copyBtn = document.getElementById('copyBtn');
    const manifestUrl = document.getElementById('manifestUrl');
    function encodeConfig(config) {{
      const json = JSON.stringify(config);
      return 'cfg-' + btoa(unescape(encodeURIComponent(json)))
        .replace(/[+]/g, '-')
        .split('/').join('_')
        .replace(/=+$/g, '');
    }}
    function currentManifest() {{
      const config = Object.fromEntries(fields.map((field) => [field.id, field.value]));
      const payload = encodeConfig(config);
      return `{base_url}/${{payload}}/manifest.json`;
    }}
    function refresh() {{
      manifestUrl.textContent = currentManifest();
    }}
    async function copyManifest() {{
      try {{
        await navigator.clipboard.writeText(currentManifest());
        copyBtn.textContent = 'Manifest URL copied';
        setTimeout(() => {{ copyBtn.textContent = 'Copy manifest URL'; }}, 1500);
      }} catch (err) {{
        copyBtn.textContent = 'Copy failed';
        setTimeout(() => {{ copyBtn.textContent = 'Copy manifest URL'; }}, 1500);
      }}
    }}
    fields.forEach((field) => field.addEventListener('change', refresh));
    copyBtn.addEventListener('click', copyManifest);
    installBtn.addEventListener('click', async () => {{
      await copyManifest();
      window.location.href = currentManifest().replace('https://', 'stremio://').replace('http://', 'stremio://');
    }});
    refresh();
  </script>
</body>
</html>"""


def _find_catalog(catalog_id: str) -> dict:
    catalog = settings.CATALOGS_BY_ID.get(catalog_id)
    if not catalog:
        raise HTTPException(status_code=404, detail="unknown catalog")
    return catalog


def _effective_country_filter(catalog_def: dict, config: dict[str, str]) -> str | None:
    catalog_country = catalog_def["country_code"]
    if catalog_country is not None:
        return catalog_country

    return None


def get_channels() -> list[CatalogChannel]:
    started_at = perf_counter()
    return channels_cache.remember_stale(
        "channels",
        settings.CHANNELS_CACHE_TTL_SECONDS,
        settings.CHANNELS_CACHE_STALE_SECONDS,
        lambda: _timed_call("channels scrape", scrape_channels, started_at=started_at),
    )


def get_channel_index() -> dict[int, CatalogChannel]:
    return channel_index_cache.remember(
        "channels:index",
        settings.CHANNELS_CACHE_TTL_SECONDS,
        lambda: {channel.channel_id: channel for channel in get_channels()},
    )


def get_cached_channel(channel_id: int) -> CatalogChannel | None:
    channel_index = channel_index_cache.get("channels:index")
    if channel_index is None:
        return None
    return channel_index.get(channel_id)


def refresh_schedule() -> list[LiveEvent]:
    started_at = perf_counter()
    events = scrape_schedule(get_channel_index())
    schedule_cache.set("schedule", events, settings.SCHEDULE_CACHE_TTL_SECONDS)
    schedule_index_cache.set(
        "schedule:index",
        {event.meta_id: event for event in events},
        settings.SCHEDULE_CACHE_TTL_SECONDS,
    )
    _log_timing("schedule refresh", started_at, events=len(events))
    return events


def get_schedule() -> list[LiveEvent]:
    started_at = perf_counter()
    return schedule_cache.remember_stale(
        "schedule",
        settings.SCHEDULE_CACHE_TTL_SECONDS,
        settings.SCHEDULE_CACHE_STALE_SECONDS,
        lambda: _timed_call("schedule scrape", lambda: scrape_schedule(get_channel_index()), started_at=started_at),
    )


def get_schedule_index() -> dict[str, LiveEvent]:
    return schedule_index_cache.remember(
        "schedule:index",
        settings.SCHEDULE_CACHE_TTL_SECONDS,
        lambda: {event.meta_id: event for event in get_schedule()},
    )


def get_wrapper(channel_id: int) -> WrapperCatalog:
    started_at = perf_counter()
    return watch_cache.remember_stale(
        f"watch:{channel_id}",
        settings.WATCH_CACHE_TTL_SECONDS,
        settings.WATCH_CACHE_STALE_SECONDS,
        lambda: _timed_call("watch fetch", lambda: fetch_wrapper(channel_id), started_at=started_at, channel_id=channel_id),
    )


def _country_label(country_code: str) -> str:
    return settings.COUNTRY_LABELS[country_code]


def _ordered_player_targets(wrapper: WrapperCatalog) -> list[tuple[str, str]]:
    targets: list[tuple[str, str]] = []
    seen_urls: set[str] = set()

    for alternate in wrapper.player.alternates:
        if not alternate.active or alternate.url in seen_urls:
            continue
        seen_urls.add(alternate.url)
        targets.append((alternate.label or "alternate", alternate.url))

    if wrapper.player.primary.url and wrapper.player.primary.url not in seen_urls:
        seen_urls.add(wrapper.player.primary.url)
        targets.append((wrapper.player.primary.label, wrapper.player.primary.url))

    for alternate in wrapper.player.alternates:
        if alternate.active or alternate.url in seen_urls:
            continue
        seen_urls.add(alternate.url)
        targets.append((alternate.label or "alternate", alternate.url))

    return targets


def _ordered_live_channels(event: LiveEvent, config: dict[str, str]) -> list:
    unique_channels = list({channel.channel_id: channel for channel in event.channels}.values())
    configured_country = _preferred_country_code(config)
    if configured_country is not None:
        preferred_country_codes = [configured_country]
    else:
        preferred_country_codes = [code for code in event.country_codes if code != "global"]

    def key(channel) -> tuple[int, int, int]:
        cached = 0 if live_channel_stream_cache.get(f"live-channel:{channel.channel_id}") is not None else 1
        if preferred_country_codes:
            if channel.country_code in preferred_country_codes:
                country_weight = 0
            elif channel.country_code != "global":
                country_weight = 1
            else:
                country_weight = 2
        else:
            country_weight = 0 if channel.country_code != "global" else 1

        return (country_weight, cached, channel.channel_id)

    return sorted(unique_channels, key=key)


def _channel_preview(channel: CatalogChannel, request: Request) -> dict:
    return {
        "id": channel.meta_id,
        "type": "tv",
        "name": channel.name,
        "poster": _poster_url(request, channel.meta_id),
        "posterShape": "poster",
        "description": f"{channel.country_label} channel â€¢ ID {channel.channel_id}",
        "genres": [channel.country_label],
    }


def _event_preview(event: LiveEvent, request: Request, config: dict[str, str]) -> dict:
    country_labels = [_country_label(code) for code in event.country_codes]
    day_label, time_text = _event_display_values(event, config)
    return {
        "id": event.meta_id,
        "type": "tv",
        "name": event.title,
        "poster": _poster_url(request, event.meta_id),
        "posterShape": "poster",
        "description": f"{day_label} â€¢ {time_text} â€¢ {event.category}",
        "genres": [event.category, *country_labels],
    }


def _parse_meta_id(meta_id: str) -> tuple[str, int | str]:
    if meta_id.startswith("dlhd:ch:"):
        channel_id = meta_id.removeprefix("dlhd:ch:")
        if channel_id.isdigit():
            return "channel", int(channel_id)
    if meta_id.startswith("dlhd:live:"):
        return "live", meta_id
    raise HTTPException(status_code=404, detail="unknown meta id")


def _manifest_to_stream(
    *,
    request: Request,
    display_name: str,
    description: str,
    manifest_url: str,
    referer: str,
    player_type: str,
) -> dict:
    if player_type.lower() == "hls":
        return {
            "name": display_name,
            "description": description,
            "url": _proxy_media_url(request, manifest_url, referer, filename_hint="stream.m3u8"),
            "behaviorHints": {
                "notWebReady": True,
                "filename": "stream.m3u8",
            },
        }

    filename = manifest_url.rstrip("/").split("/")[-1] or "stream.m3u8"
    return {
        "name": display_name,
        "description": description,
        "url": manifest_url,
        "behaviorHints": {
            "notWebReady": True,
            "filename": filename,
            "proxyHeaders": {
                "request": {
                    "Referer": referer,
                    "User-Agent": DEFAULT_HEADERS["User-Agent"],
                }
            },
        },
    }


def _proxy_media_url(
    request: Request,
    upstream_url: str,
    referer: str,
    *,
    filename_hint: str | None = None,
) -> str:
    parsed = urlparse(upstream_url)
    filename = filename_hint or (parsed.path.rsplit("/", 1)[-1] or "stream.bin")
    safe_filename = quote(filename, safe="._-")
    query = urlencode({"url": upstream_url, "referer": referer})
    return f"{_service_base_url(request)}/proxy/{safe_filename}?{query}"


def _follow_proxy_redirects(session, url: str, headers: dict[str, str], *, log_fields: dict[str, object] | None = None):
    current_url = _validate_proxy_url(url, "url")
    response = None
    for _ in range(settings.PROXY_MAX_REDIRECTS + 1):
        response = session.get(
            current_url,
            timeout=settings.HTTP_TIMEOUT_SECONDS,
            stream=True,
            allow_redirects=False,
            headers=headers,
        )

        if 300 <= response.status_code < 400 and response.headers.get("Location"):
            location = urljoin(current_url, response.headers["Location"])
            response.close()
            allowed, reason, redirect_kind = _proxy_redirect_decision(current_url, location)
            if not allowed:
                if log_fields is not None:
                    log_event(
                        LOGGER,
                        logging.WARNING,
                        "proxy_redirect_rejected",
                        **log_fields,
                        from_host=urlparse(current_url).hostname,
                        to_host=urlparse(location).hostname,
                        redirect_kind=redirect_kind,
                        reason=reason,
                    )
                raise HTTPException(status_code=403, detail="disallowed redirect host")
            if log_fields is not None:
                log_event(
                    LOGGER,
                    logging.INFO,
                    "proxy_redirect_allowed",
                    **log_fields,
                    from_host=urlparse(current_url).hostname,
                    to_host=urlparse(location).hostname,
                    redirect_kind=redirect_kind,
                )
            current_url = location
            continue

        return response

    if log_fields is not None:
        log_event(LOGGER, logging.WARNING, "proxy_redirect_rejected", **log_fields, reason="too_many_redirects")
    raise HTTPException(status_code=502, detail="too many upstream redirects")


def _rewrite_playlist_line(request: Request, line: str, playlist_url: str, referer: str) -> str:
    stripped = line.strip()
    if not stripped:
        return line

    if stripped.startswith("#"):
        def repl(match: re.Match[str]) -> str:
            absolute = urljoin(playlist_url, match.group(1))
            return f'URI="{_proxy_media_url(request, absolute, referer)}"'

        return HLS_URI_ATTR_RE.sub(repl, line)

    absolute = urljoin(playlist_url, stripped)
    return _proxy_media_url(request, absolute, referer)


def _rewrite_hls_playlist(request: Request, body: str, playlist_url: str, referer: str) -> str:
    rewritten = [
        _rewrite_playlist_line(request, line, playlist_url, referer)
        for line in body.splitlines()
    ]
    return "\n".join(rewritten) + ("\n" if body.endswith("\n") else "")


def _stream_upstream(upstream):
    try:
        for chunk in upstream.iter_content(chunk_size=256 * 1024):
            if chunk:
                yield chunk
    finally:
        upstream.close()


def _default_video(meta_id: str, title: str) -> dict:
    return {
        "id": meta_id,
        "title": title,
        "released": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "available": True,
    }


def _timed_call(label: str, factory, *, started_at: float | None = None, **fields: object):
    timing_started_at = perf_counter() if started_at is None else started_at
    value = factory()
    _log_timing(label, timing_started_at, **fields)
    return value


def _remember_streams(cache_key: str, factory) -> list[dict]:
    cached = stream_cache.get(cache_key)
    if cached is not None:
        return cached

    streams = factory()
    ttl_seconds = (
        settings.STREAM_CACHE_TTL_SECONDS
        if streams
        else settings.FAILED_STREAM_CACHE_TTL_SECONDS
    )
    return stream_cache.set(cache_key, streams, ttl_seconds)


def _build_channel_streams(channel: CatalogChannel, request: Request, config: dict[str, str]) -> list[dict]:
    def factory() -> list[dict]:
        started_at = perf_counter()
        try:
            wrapper = get_wrapper(channel.channel_id)
        except Exception as exc:  # noqa: BLE001
            log_event(
                LOGGER,
                logging.WARNING,
                "stream_candidate_rejected",
                channel_id=channel.channel_id,
                reason="watch_fetch_failed",
                error=exc.__class__.__name__,
            )
            return []

        targets = _ordered_player_targets(wrapper)

        streams: list[dict] = []
        seen_urls: set[str] = set()
        for label, player_url in targets[:settings.CHANNEL_STREAM_MAX_ATTEMPTS]:
            log_event(
                LOGGER,
                logging.DEBUG,
                "channel_player_attempt",
                channel_id=channel.channel_id,
                provider=label,
                player_host=urlparse(player_url).hostname,
            )
            try:
                resolution = resolver.resolve_player(label, player_url)
            except Exception as exc:  # noqa: BLE001
                log_event(
                    LOGGER,
                    logging.WARNING,
                    "stream_candidate_rejected",
                    channel_id=channel.channel_id,
                    provider=label,
                    reason="resolver_exception",
                    error=exc.__class__.__name__,
                    player_host=urlparse(player_url).hostname,
                )
                continue

            if not resolution.manifests:
                log_event(
                    LOGGER,
                    logging.DEBUG,
                    "stream_candidate_rejected",
                    channel_id=channel.channel_id,
                    provider=resolution.label,
                    reason="no_manifests",
                    player_host=urlparse(player_url).hostname,
                )

            for manifest in resolution.manifests:
                if manifest.url in seen_urls:
                    log_event(
                        LOGGER,
                        logging.DEBUG,
                        "stream_candidate_rejected",
                        channel_id=channel.channel_id,
                        provider=resolution.label,
                        reason="duplicate_manifest",
                        stream_kind=manifest.player_type,
                        **proxy_url_fields(manifest.url),
                    )
                    continue
                if manifest.player_type.lower() == "hls" and not _valid_hls_stream(manifest.url, manifest.found_at_url):
                    log_event(
                        LOGGER,
                        logging.WARNING,
                        "stream_candidate_rejected",
                        channel_id=channel.channel_id,
                        provider=resolution.label,
                        reason="invalid_hls_manifest",
                        hls_reason=_hls_validation_reason(manifest.url, manifest.found_at_url),
                        stream_kind=manifest.player_type,
                        **proxy_url_fields(manifest.url),
                    )
                    continue
                seen_urls.add(manifest.url)
                log_event(
                    LOGGER,
                    logging.INFO,
                    "stream_candidate_selected",
                    channel_id=channel.channel_id,
                    provider=resolution.label,
                    stream_kind=manifest.player_type,
                    **proxy_url_fields(manifest.url),
                )
                streams.append(
                    _manifest_to_stream(
                        request=request,
                        display_name=settings.ADDON_NAME,
                        description=(
                            f"{channel.name} â€¢ {channel.country_label} â€¢ "
                            f"{resolution.label} â€¢ {manifest.player_type.upper()}"
                        ),
                        manifest_url=manifest.url,
                        referer=manifest.found_at_url,
                        player_type=manifest.player_type,
                    )
                )
                if len(streams) >= _channel_stream_result_limit(config):
                    return streams

            if streams:
                _log_timing("channel stream resolve", started_at, channel_id=channel.channel_id, streams=len(streams))
                return streams

        _log_timing("channel stream resolve", started_at, channel_id=channel.channel_id, streams=0)
        return streams

    return _remember_streams(
        (
            f"stream:channel:{channel.channel_id}:{_service_base_url(request)}:"
            f"{_config_fingerprint(config, ('channelStreamResults',))}"
        ),
        factory,
    )


def _get_live_channel_payload(linked_channel) -> tuple[dict[str, str] | None, str | None]:
    cache_key = f"live-channel:{linked_channel.channel_id}"
    cached = live_channel_stream_cache.get(cache_key)
    if cached is not None:
        return cached, None

    started_at = perf_counter()

    try:
        wrapper = get_wrapper(linked_channel.channel_id)
    except Exception:
        return None, "watch_fetch_failed"

    try:
        resolutions = resolver.resolve_wrapper(
            wrapper,
            resolve_all=False,
            stop_after_first_success=True,
        )
    except Exception:  # noqa: BLE001
        return None, "resolver_exception"

    for resolution in resolutions:
        if not resolution.manifests:
            continue
        manifest = resolution.manifests[0]
        payload = {
            "url": manifest.url,
            "referer": manifest.found_at_url,
            "player_type": manifest.player_type.upper(),
        }
        live_channel_stream_cache.set(
            cache_key,
            payload,
            settings.LIVE_CHANNEL_CACHE_TTL_SECONDS,
        )
        _log_timing("live channel resolve", started_at, channel_id=linked_channel.channel_id, success=True)
        return payload, None

    _log_timing("live channel resolve", started_at, channel_id=linked_channel.channel_id, success=False)
    return None, "no_manifests"


def _resolve_live_channel_stream(event: LiveEvent, linked_channel, request: Request) -> dict | None:
    log_event(
        LOGGER,
        logging.DEBUG,
        "live_channel_attempt",
        event_id=event.meta_id,
        channel_id=linked_channel.channel_id,
    )
    payload, rejection_reason = _get_live_channel_payload(linked_channel)
    if not payload:
        log_event(
            LOGGER,
            logging.INFO,
            "live_channel_rejected",
            event_id=event.meta_id,
            channel_id=linked_channel.channel_id,
            reason=rejection_reason,
        )
        return None
    if payload["player_type"].lower() == "hls" and not _valid_hls_stream(payload["url"], payload["referer"]):
        log_event(
            LOGGER,
            logging.WARNING,
            "live_channel_rejected",
            event_id=event.meta_id,
            channel_id=linked_channel.channel_id,
            reason="invalid_hls_manifest",
            hls_reason=_hls_validation_reason(payload["url"], payload["referer"]),
            stream_kind=payload["player_type"],
            **proxy_url_fields(payload["url"]),
        )
        return None

    log_event(
        LOGGER,
        logging.INFO,
        "live_channel_selected",
        event_id=event.meta_id,
        channel_id=linked_channel.channel_id,
        stream_kind=payload["player_type"],
        **proxy_url_fields(payload["url"]),
    )

    return _manifest_to_stream(
        request=request,
        display_name=settings.ADDON_NAME,
        description=f"{linked_channel.name} â€¢ {linked_channel.country_label} â€¢ {event.title} â€¢ {payload['player_type']}",
        manifest_url=payload["url"],
        referer=payload["referer"],
        player_type=payload["player_type"],
    )


def _build_live_streams(event: LiveEvent, request: Request, config: dict[str, str]) -> list[dict]:
    def factory() -> list[dict]:
        started_at = perf_counter()
        streams: list[dict] = []
        seen_urls: set[str] = set()

        started_at = perf_counter()
        attempt_channels = _ordered_live_channels(event, config)[:settings.LIVE_STREAM_MAX_ATTEMPTS]
        if not attempt_channels:
            return streams

        max_workers = max(1, min(settings.LIVE_STREAM_MAX_WORKERS, len(attempt_channels)))
        for offset in range(0, len(attempt_channels), max_workers):
            if perf_counter() - started_at >= settings.LIVE_STREAM_BUDGET_SECONDS:
                log_event(
                    LOGGER,
                    logging.WARNING,
                    "live_event_budget_exhausted",
                    event_id=event.meta_id,
                    streams=len(streams),
                    reason="budget_exhausted",
                    duration_ms=round((perf_counter() - started_at) * 1000),
                )
                break

            batch = attempt_channels[offset:offset + max_workers]
            for linked_channel in batch:
                stream = _resolve_live_channel_stream(event, linked_channel, request)
                if not stream:
                    continue
                if stream["url"] in seen_urls:
                    log_event(
                        LOGGER,
                        logging.DEBUG,
                        "live_channel_rejected",
                        event_id=event.meta_id,
                        channel_id=linked_channel.channel_id,
                        reason="duplicate_manifest",
                    )
                    continue

                seen_urls.add(stream["url"])
                streams.append(stream)
                if len(streams) >= _live_stream_result_limit(config):
                    _log_timing("live event stream resolve", started_at, event_id=event.meta_id, streams=len(streams))
                    return streams

        _log_timing("live event stream resolve", started_at, event_id=event.meta_id, streams=len(streams))
        return streams

    return _remember_streams(
        (
            f"stream:event:{event.meta_id}:{_service_base_url(request)}:"
            f"{_config_fingerprint(config, ('preferredCountryCode', 'liveStreamResults'))}"
        ),
        factory,
    )


def _find_event(meta_id: str) -> LiveEvent | None:
    event = get_schedule_index().get(meta_id)
    if event is not None:
        return event

    refresh_schedule()
    return get_schedule_index().get(meta_id)


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/configure", status_code=307)


@app.get("/configure")
def configure(request: Request) -> HTMLResponse:
    return HTMLResponse(_configure_page(request))


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/manifest.json")
@app.get("/{config}/manifest.json")
def manifest(request: Request, config: str | None = None) -> JSONResponse:
    user_config = _parse_user_config_for_request(request, config)
    manifest_payload = build_manifest(_service_base_url(request), configured=bool(user_config), user_config=user_config)
    log_event(
        LOGGER,
        logging.INFO,
        "manifest_served",
        **_request_log_fields(
            request,
            configured=bool(user_config),
            catalog_count=len(manifest_payload["catalogs"]),
            user_config_hash=config_fingerprint(user_config),
        ),
    )
    return JSONResponse(manifest_payload)


@app.head("/manifest.json")
@app.head("/{config}/manifest.json")
def manifest_head(config: str | None = None) -> Response:
    return Response(media_type="application/json")


@app.get("/catalog/tv/{catalog_id}.json")
@app.get("/catalog/tv/{catalog_id}/{extra:path}.json")
@app.get("/{config}/catalog/tv/{catalog_id}.json")
@app.get("/{config}/catalog/tv/{catalog_id}/{extra:path}.json")
def catalog(request: Request, catalog_id: str, extra: str | None = None, config: str | None = None) -> JSONResponse:
    catalog_def = _find_catalog(catalog_id)
    extra_props = _parse_extra(extra)
    user_config = _parse_user_config_for_request(request, config)
    search = extra_props.get("search") or None
    skip = _parse_skip(extra_props.get("skip"))
    country_filter = _effective_country_filter(catalog_def, user_config)
    stale_after_minutes = _event_stale_after_minutes(user_config)

    if catalog_def["kind"] == "channels":
        items = filter_channels(
            get_channels(),
            country_code=country_filter,
            search=search,
            skip=skip,
        )
        metas = [_channel_preview(channel, request) for channel in items]
    else:
        items = filter_schedule(
            get_schedule(),
            country_code=country_filter,
            search=search,
            skip=skip,
            stale_after_minutes=stale_after_minutes,
        )
        metas = [_event_preview(event, request, user_config) for event in items]

    log_event(
        LOGGER,
        logging.INFO,
        "catalog_served",
        **_request_log_fields(
            request,
            catalog_id=catalog_id,
            search=search,
            skip=skip,
            result_count=len(metas),
            country_filter=country_filter,
            stale_after_minutes=stale_after_minutes if catalog_def["kind"] == "live" else None,
            user_config_hash=config_fingerprint(user_config),
        ),
    )
    if not metas:
        log_event(
            LOGGER,
            logging.INFO,
            "catalog_empty",
            **_request_log_fields(request, catalog_id=catalog_id, search=search, skip=skip, country_filter=country_filter),
        )

    return JSONResponse({"metas": metas})


@app.get("/meta/tv/{meta_id:path}.json")
@app.get("/{config}/meta/tv/{meta_id:path}.json")
def meta(request: Request, meta_id: str, config: str | None = None) -> JSONResponse:
    kind, value = _parse_meta_id(meta_id)
    user_config = _parse_user_config_for_request(request, config)
    request_fields = _request_log_fields(request, meta_id=meta_id, user_config_hash=config_fingerprint(user_config))
    if kind == "channel":
        channel_id = value
        channel_index = get_channel_index()
        channel = channel_index.get(channel_id)
        wrapper = watch_cache.get(f"watch:{channel_id}")
        if channel is None:
            try:
                wrapper = wrapper or _wrapper_or_http_error(channel_id)
            except HTTPException:
                log_event(LOGGER, logging.WARNING, "meta_not_found", **request_fields, kind=kind, channel_id=channel_id)
                raise
            channel = CatalogChannel(
                channel_id=channel_id,
                name=wrapper.channel.name or wrapper.page.title or f"Channel {channel_id}",
                watch_url=f"{settings.BASE_SITE_URL}/watch.php?id={channel_id}",
                search_hint=None,
                group_letter=None,
                country_code="global",
                country_label=settings.COUNTRY_LABELS["global"],
            )

        meta_payload = {
            "id": channel.meta_id,
            "type": "tv",
            "name": channel.name,
            "poster": _poster_url(request, channel.meta_id),
            "posterShape": "poster",
            "background": _poster_url(request, channel.meta_id),
            "description": (
                wrapper.page.description
                if wrapper is not None and wrapper.page.description
                else f"{channel.country_label} channel â€¢ ID {channel.channel_id}"
            ),
            "genres": [channel.country_label],
            "videos": [_default_video(channel.meta_id, channel.name)],
            "behaviorHints": {"defaultVideoId": channel.meta_id},
        }
        log_event(LOGGER, logging.INFO, "meta_served", **request_fields, kind=kind, channel_id=channel_id)
        return JSONResponse({"meta": meta_payload})

    event = _find_event(value)
    if event is None:
        log_event(LOGGER, logging.WARNING, "meta_not_found", **request_fields, kind=kind, event_id=value)
        raise HTTPException(status_code=404, detail="event not found")
    event_day_label, event_time_text = _event_display_values(event, user_config)

    meta_payload = {
        "id": event.meta_id,
        "type": "tv",
        "name": event.title,
        "poster": _poster_url(request, event.meta_id),
        "posterShape": "poster",
        "background": _poster_url(request, event.meta_id),
        "description": (
            f"{event_day_label} â€¢ {event_time_text} â€¢ {event.category}\n"
            f"Channels: {', '.join(channel.name for channel in event.channels)}"
        ),
        "genres": [event.category, *[_country_label(code) for code in event.country_codes]],
        "videos": [_default_video(event.meta_id, event.title)],
        "behaviorHints": {"defaultVideoId": event.meta_id},
    }
    log_event(LOGGER, logging.INFO, "meta_served", **request_fields, kind=kind, event_id=event.meta_id)
    return JSONResponse({"meta": meta_payload})


@app.get("/stream/tv/{meta_id:path}.json")
@app.get("/{config}/stream/tv/{meta_id:path}.json")
def stream(request: Request, meta_id: str, config: str | None = None) -> JSONResponse:
    kind, value = _parse_meta_id(meta_id)
    user_config = _parse_user_config_for_request(request, config)
    request_fields = _request_log_fields(request, meta_id=meta_id, user_config_hash=config_fingerprint(user_config), kind=kind)
    log_event(LOGGER, logging.INFO, "stream_request_start", **request_fields)
    if kind == "channel":
        channel = get_cached_channel(value)
        if channel is None:
            try:
                wrapper = _wrapper_or_http_error(value)
            except HTTPException:
                log_event(LOGGER, logging.WARNING, "stream_request_end", **request_fields, channel_id=value, stream_count=0, status_code=404)
                raise
            channel = CatalogChannel(
                channel_id=value,
                name=wrapper.channel.name or wrapper.page.title or f"Channel {value}",
                watch_url=f"{settings.BASE_SITE_URL}/watch.php?id={value}",
                search_hint=None,
                group_letter=None,
                country_code="global",
                country_label=settings.COUNTRY_LABELS["global"],
            )
        streams = _build_channel_streams(channel, request, user_config)
        log_event(
            LOGGER,
            logging.INFO,
            "stream_request_end",
            **request_fields,
            channel_id=channel.channel_id,
            stream_count=len(streams),
            status_code=200,
        )
        if not streams:
            log_event(LOGGER, logging.INFO, "stream_no_results", **request_fields, channel_id=channel.channel_id)
        return JSONResponse({"streams": streams})

    event = _find_event(value)
    if event is None:
        log_event(LOGGER, logging.WARNING, "stream_request_end", **request_fields, event_id=value, stream_count=0, status_code=404)
        raise HTTPException(status_code=404, detail="event not found")
    streams = _build_live_streams(event, request, user_config)
    log_event(
        LOGGER,
        logging.INFO,
        "stream_request_end",
        **request_fields,
        event_id=event.meta_id,
        stream_count=len(streams),
        status_code=200,
    )
    if not streams:
        log_event(LOGGER, logging.INFO, "stream_no_results", **request_fields, event_id=event.meta_id)
    return JSONResponse({"streams": streams})


@app.get("/proxy/{filename:path}")
def proxy_media(
    request: Request,
    filename: str,
    url: str = Query(...),
    referer: str = Query(...),
) -> Response:
    started_at = perf_counter()
    request_fields = _request_log_fields(request, filename=filename)
    log_event(
        LOGGER,
        logging.INFO,
        "proxy_request_start",
        **request_fields,
        upstream_host=urlparse(url).hostname,
        referer_host=urlparse(referer).hostname,
    )

    try:
        validated_url = _validate_proxy_url(url, "url")
    except HTTPException as exc:
        _, reason = _proxy_url_error(url)
        log_event(
            LOGGER,
            logging.WARNING,
            "proxy_url_rejected",
            **request_fields,
            field="url",
            reason=reason,
            status_code=exc.status_code,
            **proxy_url_fields(url),
        )
        raise

    try:
        validated_referer = _validate_proxy_url(referer, "referer")
    except HTTPException as exc:
        _, reason = _proxy_url_error(referer)
        log_event(
            LOGGER,
            logging.WARNING,
            "proxy_url_rejected",
            **request_fields,
            field="referer",
            reason=reason,
            status_code=exc.status_code,
            referer_host=urlparse(referer).hostname,
        )
        raise

    playlist_cache_key = None
    if filename.lower().endswith(".m3u8"):
        playlist_cache_key = _playlist_cache_key(_service_base_url(request), validated_url, validated_referer)
        cached_playlist = playlist_cache.get(playlist_cache_key)
        if cached_playlist is not None:
            log_event(
                LOGGER,
                logging.INFO,
                "proxy_playlist_cache_hit",
                **request_fields,
                duration_ms=round((perf_counter() - started_at) * 1000),
                cache_name="playlist",
                cache_status="hit",
                **proxy_url_fields(validated_url),
            )
            return Response(content=cached_playlist, media_type="application/vnd.apple.mpegurl")

    session = get_pooled_session()
    headers = {"Referer": validated_referer}

    range_header = request.headers.get("range")
    if range_header:
        headers["Range"] = range_header

    try:
        upstream = _follow_proxy_redirects(session, validated_url, headers, log_fields=request_fields)
        upstream.raise_for_status()
    except requests.RequestException as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        log_event(
            LOGGER,
            logging.WARNING,
            "proxy_upstream_error",
            **request_fields,
            status_code=status_code,
            reason=exc.__class__.__name__,
            **proxy_url_fields(validated_url),
        )
        raise

    content_type = upstream.headers.get("Content-Type", "application/octet-stream").split(";", 1)[0].strip()

    if playlist_cache_key is not None:
        body = upstream.text
        upstream.close()

        if not _looks_like_hls_playlist(body):
            log_event(
                LOGGER,
                logging.INFO,
                "proxy_non_hls_passthrough",
                **request_fields,
                duration_ms=round((perf_counter() - started_at) * 1000),
                content_type=content_type or "text/plain",
                **proxy_url_fields(upstream.url),
            )
            return Response(content=body, media_type=content_type or "text/plain")

        rewritten = _rewrite_hls_playlist(request, body, upstream.url, validated_referer)
        playlist_cache.set(
            playlist_cache_key,
            rewritten,
            settings.HLS_PLAYLIST_CACHE_TTL_SECONDS,
        )
        log_event(
            LOGGER,
            logging.INFO,
            "proxy_playlist_rewrite",
            **request_fields,
            duration_ms=round((perf_counter() - started_at) * 1000),
            cache_name="playlist",
            **proxy_url_fields(upstream.url),
        )
        return Response(content=rewritten, media_type="application/vnd.apple.mpegurl")

    response_headers = {}
    for header_name in ("Accept-Ranges", "Content-Length", "Content-Range"):
        header_value = upstream.headers.get(header_name)
        if header_value:
            response_headers[header_name] = header_value

    if content_type in {"application/javascript", "text/javascript", "text/plain", "text/txt"}:
        content_type = "application/octet-stream"

    log_event(
        LOGGER,
        logging.INFO,
        "proxy_binary_passthrough",
        **request_fields,
        duration_ms=round((perf_counter() - started_at) * 1000),
        status_code=upstream.status_code,
        content_type=content_type,
        **proxy_url_fields(upstream.url),
    )

    return StreamingResponse(
        _stream_upstream(upstream),
        status_code=upstream.status_code,
        media_type=content_type,
        headers=response_headers,
    )


@app.head("/proxy/{filename:path}")
def proxy_media_head(filename: str) -> Response:
    media_type = "application/vnd.apple.mpegurl" if filename.lower().endswith(".m3u8") else "application/octet-stream"
    return Response(media_type=media_type)


@app.get("/assets/logo.svg")
def logo() -> Response:
    svg = render_svg_poster(settings.ADDON_NAME, "Stremio addon", "global")
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/assets/background.svg")
def background() -> Response:
    svg = render_svg_poster(settings.ADDON_NAME, settings.ADDON_DESCRIPTION, "global")
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/assets/poster/{meta_id:path}.svg")
def poster(meta_id: str) -> Response:
    try:
        kind, value = _parse_meta_id(meta_id)
    except HTTPException:
        svg = render_svg_poster(settings.ADDON_NAME, settings.ADDON_DESCRIPTION, "global")
        return Response(content=svg, media_type="image/svg+xml")

    if kind == "channel":
        channel = get_channel_index().get(value)
        if channel is None:
            wrapper = _wrapper_or_http_error(value)
            title = wrapper.channel.name or wrapper.page.title or f"Channel {value}"
            subtitle = settings.COUNTRY_LABELS["global"]
            accent = "global"
        else:
            title = channel.name
            subtitle = channel.country_label
            accent = channel.country_code
        svg = render_svg_poster(title, subtitle, accent)
        return Response(content=svg, media_type="image/svg+xml")

    event = _find_event(value)
    if event is None:
        svg = render_svg_poster("Live Event", "Unavailable", "global")
        return Response(content=svg, media_type="image/svg+xml")

    accent = event.country_codes[0] if event.country_codes else "global"
    subtitle = f"{event.time_text} â€¢ {event.category}"
    svg = render_svg_poster(event.title, subtitle, accent)
    return Response(content=svg, media_type="image/svg+xml")

