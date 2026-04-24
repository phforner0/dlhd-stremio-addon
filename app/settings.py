from __future__ import annotations

import os

from app.resolve.providers import default_proxy_allowed_hosts


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _csv_hosts(raw_value: str | None) -> tuple[str, ...]:
    if not raw_value:
        return ()
    return tuple(host.strip().lower() for host in raw_value.split(",") if host.strip())


def _merge_allowed_hosts(raw_value: str | None, *, defaults: tuple[str, ...], strict: bool) -> tuple[str, ...]:
    configured = _csv_hosts(raw_value)
    if strict and configured:
        return configured
    if not configured:
        return defaults
    merged: list[str] = []
    for host in (*defaults, *configured):
        if host not in merged:
            merged.append(host)
    return tuple(merged)


def _default_artwork_allowed_hosts() -> tuple[str, ...]:
    merged: list[str] = []
    for host in (
        *default_proxy_allowed_hosts(),
        "dlstreams.com",
        ".dlstreams.com",
        "r2.thesportsdb.com",
        ".r2.thesportsdb.com",
        "www.thesportsdb.com",
        ".thesportsdb.com",
    ):
        if host not in merged:
            merged.append(host)
    return tuple(merged)


def _build_proxy_allowed_hosts(raw_value: str | None, *, strict: bool) -> tuple[str, ...]:
    return _merge_allowed_hosts(raw_value, defaults=default_proxy_allowed_hosts(), strict=strict)

BASE_SITE_URL = os.getenv("DLHD_BASE_URL", "https://dlstreams.top").rstrip("/")
ADDON_ID = os.getenv("DLHD_ADDON_ID", "com.dlhd.stremio")
ADDON_NAME = os.getenv("DLHD_ADDON_NAME", "DLHD Streams")
ADDON_VERSION = os.getenv("DLHD_ADDON_VERSION", "0.1.3")
ADDON_DESCRIPTION = os.getenv(
    "DLHD_ADDON_DESCRIPTION",
    "Scraped live channels and schedule grouped by country for Stremio.",
)
LOG_LEVEL = os.getenv("DLHD_LOG_LEVEL", "INFO").upper()
LOG_FORMAT = os.getenv("DLHD_LOG_FORMAT", "text").lower()
LOG_REQUEST_START = _env_flag("DLHD_LOG_REQUEST_START", False)
RESOURCE_LOG_ENABLED = _env_flag("DLHD_RESOURCE_LOG_ENABLED", True)
RESOURCE_LOG_RSS_MB_THRESHOLD = int(os.getenv("DLHD_RESOURCE_LOG_RSS_MB_THRESHOLD", "350"))
RESOURCE_LOG_INTERVAL_SECONDS = int(os.getenv("DLHD_RESOURCE_LOG_INTERVAL_SECONDS", "60"))

HTTP_TIMEOUT_SECONDS = int(os.getenv("DLHD_HTTP_TIMEOUT", "20"))
PROXY_MAX_REDIRECTS = int(os.getenv("DLHD_PROXY_MAX_REDIRECTS", "5"))
PROXY_ALLOWED_HOSTS_STRICT = _env_flag("DLHD_PROXY_ALLOWED_HOSTS_STRICT", False)
PROXY_ALLOWED_HOSTS = _merge_allowed_hosts(
    os.getenv("DLHD_PROXY_ALLOWED_HOSTS"),
    defaults=default_proxy_allowed_hosts(),
    strict=PROXY_ALLOWED_HOSTS_STRICT,
)
ARTWORK_ENABLED = _env_flag("DLHD_ARTWORK_ENABLED", True)
ARTWORK_CHANNELS_UPSTREAM_ENABLED = _env_flag("DLHD_ARTWORK_CHANNELS_UPSTREAM", True)
ARTWORK_EVENTS_PROVIDER = os.getenv("DLHD_ARTWORK_EVENTS_PROVIDER", "thesportsdb").strip().lower()
ARTWORK_ALLOWED_HOSTS_STRICT = _env_flag("DLHD_ARTWORK_ALLOWED_HOSTS_STRICT", False)
ARTWORK_ALLOWED_HOSTS = _merge_allowed_hosts(
    os.getenv("DLHD_ARTWORK_ALLOWED_HOSTS"),
    defaults=_default_artwork_allowed_hosts(),
    strict=ARTWORK_ALLOWED_HOSTS_STRICT,
)
THE_SPORTS_DB_API_KEY = os.getenv("DLHD_THE_SPORTS_DB_API_KEY", "3").strip() or "3"
ARTWORK_CACHE_TTL_SECONDS = int(os.getenv("DLHD_ARTWORK_CACHE_TTL", "21600"))
ARTWORK_MISS_CACHE_TTL_SECONDS = int(os.getenv("DLHD_ARTWORK_MISS_CACHE_TTL", "1800"))
ARTWORK_CACHE_MAX_ENTRIES = int(os.getenv("DLHD_ARTWORK_CACHE_MAX_ENTRIES", "512"))
ARTWORK_IMAGE_CACHE_TTL_SECONDS = int(os.getenv("DLHD_ARTWORK_IMAGE_CACHE_TTL", "21600"))
ARTWORK_IMAGE_CACHE_MAX_ENTRIES = int(os.getenv("DLHD_ARTWORK_IMAGE_CACHE_MAX_ENTRIES", "256"))
ARTWORK_IMAGE_MAX_BYTES = int(os.getenv("DLHD_ARTWORK_IMAGE_MAX_BYTES", str(5 * 1024 * 1024)))
PLAYWRIGHT_WAIT_SECONDS = int(os.getenv("DLHD_PLAYWRIGHT_WAIT", "6"))
PLAYWRIGHT_TIMEOUT_MS = int(os.getenv("DLHD_PLAYWRIGHT_TIMEOUT_MS", "20000"))
PLAYWRIGHT_MAX_CONCURRENCY = int(os.getenv("DLHD_PLAYWRIGHT_MAX_CONCURRENCY", "4"))
PLAYWRIGHT_IGNORE_HTTPS_ERRORS = _env_flag("DLHD_PLAYWRIGHT_IGNORE_HTTPS_ERRORS", False)
PLAYWRIGHT_IGNORE_HTTPS_ERROR_HOSTS = tuple(
    host.strip().lower()
    for host in os.getenv("DLHD_PLAYWRIGHT_IGNORE_HTTPS_ERROR_HOSTS", "").split(",")
    if host.strip()
)
PLAYWRIGHT_REUSE_BROWSER = _env_flag("DLHD_PLAYWRIGHT_REUSE_BROWSER", True)
PLAYWRIGHT_BROWSER_MAX_USES = int(os.getenv("DLHD_PLAYWRIGHT_BROWSER_MAX_USES", "12"))
PLAYWRIGHT_BROWSER_MAX_IDLE_SECONDS = int(os.getenv("DLHD_PLAYWRIGHT_BROWSER_MAX_IDLE_SECONDS", "45"))
CHANNEL_STREAM_MAX_RESULTS = int(os.getenv("DLHD_CHANNEL_STREAM_MAX_RESULTS", "1"))
CHANNEL_STREAM_MAX_ATTEMPTS = int(os.getenv("DLHD_CHANNEL_STREAM_MAX_ATTEMPTS", "3"))
LIVE_STREAM_MAX_WORKERS = int(
    os.getenv("DLHD_LIVE_STREAM_MAX_WORKERS", str(min(PLAYWRIGHT_MAX_CONCURRENCY, 4)))
)
LIVE_STREAM_MAX_RESULTS = int(os.getenv("DLHD_LIVE_STREAM_MAX_RESULTS", "4"))
LIVE_STREAM_MAX_ATTEMPTS = int(os.getenv("DLHD_LIVE_STREAM_MAX_ATTEMPTS", "6"))
LIVE_STREAM_BUDGET_SECONDS = int(os.getenv("DLHD_LIVE_STREAM_BUDGET_SECONDS", "30"))
CATALOG_PAGE_SIZE = int(os.getenv("DLHD_CATALOG_PAGE_SIZE", "100"))

CHANNELS_CACHE_TTL_SECONDS = int(os.getenv("DLHD_CHANNELS_CACHE_TTL", str(12 * 60 * 60)))
CHANNELS_CACHE_STALE_SECONDS = int(os.getenv("DLHD_CHANNELS_CACHE_STALE_TTL", "900"))
CHANNELS_CACHE_MAX_ENTRIES = int(os.getenv("DLHD_CHANNELS_CACHE_MAX_ENTRIES", "4"))
CHANNEL_INDEX_CACHE_MAX_ENTRIES = int(os.getenv("DLHD_CHANNEL_INDEX_CACHE_MAX_ENTRIES", "4"))
SCHEDULE_CACHE_TTL_SECONDS = int(os.getenv("DLHD_SCHEDULE_CACHE_TTL", "120"))
SCHEDULE_CACHE_STALE_SECONDS = int(os.getenv("DLHD_SCHEDULE_CACHE_STALE_TTL", "120"))
SCHEDULE_CACHE_MAX_ENTRIES = int(os.getenv("DLHD_SCHEDULE_CACHE_MAX_ENTRIES", "8"))
SCHEDULE_INDEX_CACHE_MAX_ENTRIES = int(os.getenv("DLHD_SCHEDULE_INDEX_CACHE_MAX_ENTRIES", "8"))
WATCH_CACHE_TTL_SECONDS = int(os.getenv("DLHD_WATCH_CACHE_TTL", str(30 * 60)))
WATCH_CACHE_STALE_SECONDS = int(os.getenv("DLHD_WATCH_CACHE_STALE_TTL", "900"))
WATCH_CACHE_MAX_ENTRIES = int(os.getenv("DLHD_WATCH_CACHE_MAX_ENTRIES", "256"))
STREAM_CACHE_TTL_SECONDS = int(os.getenv("DLHD_STREAM_CACHE_TTL", "120"))
STREAM_CACHE_MAX_ENTRIES = int(os.getenv("DLHD_STREAM_CACHE_MAX_ENTRIES", "512"))
LIVE_CHANNEL_CACHE_TTL_SECONDS = int(os.getenv("DLHD_LIVE_CHANNEL_CACHE_TTL", "120"))
LIVE_CHANNEL_CACHE_MAX_ENTRIES = int(os.getenv("DLHD_LIVE_CHANNEL_CACHE_MAX_ENTRIES", "512"))
FAILED_STREAM_CACHE_TTL_SECONDS = int(os.getenv("DLHD_FAILED_STREAM_CACHE_TTL", "30"))
HLS_PLAYLIST_CACHE_TTL_SECONDS = int(os.getenv("DLHD_HLS_PLAYLIST_CACHE_TTL", "15"))
HLS_PLAYLIST_CACHE_MAX_ENTRIES = int(os.getenv("DLHD_HLS_PLAYLIST_CACHE_MAX_ENTRIES", "256"))
HLS_PLAYLIST_MAX_BYTES = int(os.getenv("DLHD_HLS_PLAYLIST_MAX_BYTES", str(1024 * 1024)))
HLS_VALIDATION_TTL_SECONDS = int(os.getenv("DLHD_HLS_VALIDATION_TTL", "120"))
HLS_VALIDATION_MAX_ENTRIES = int(os.getenv("DLHD_HLS_VALIDATION_MAX_ENTRIES", "256"))
UPSTREAM_FAILURE_THRESHOLD = int(os.getenv("DLHD_UPSTREAM_FAILURE_THRESHOLD", "3"))
UPSTREAM_FAILURE_WINDOW_SECONDS = int(os.getenv("DLHD_UPSTREAM_FAILURE_WINDOW_SECONDS", "90"))
UPSTREAM_COOLDOWN_SECONDS = int(os.getenv("DLHD_UPSTREAM_COOLDOWN_SECONDS", "180"))
UPSTREAM_HEALTH_MAX_HOSTS = int(os.getenv("DLHD_UPSTREAM_HEALTH_MAX_HOSTS", "256"))
HTTP_POOL_CONNECTIONS = int(os.getenv("DLHD_HTTP_POOL_CONNECTIONS", "32"))
HTTP_POOL_MAXSIZE = int(os.getenv("DLHD_HTTP_POOL_MAXSIZE", "64"))
SAFE_HTTP_RETRY_TOTAL = int(os.getenv("DLHD_SAFE_HTTP_RETRY_TOTAL", "2"))
SAFE_HTTP_RETRY_BACKOFF_SECONDS = float(os.getenv("DLHD_SAFE_HTTP_RETRY_BACKOFF_SECONDS", "0.25"))
PROXY_MEDIA_REDIRECT_ALLOWED_HOSTS = _merge_allowed_hosts(
    os.getenv("DLHD_PROXY_MEDIA_REDIRECT_ALLOWED_HOSTS"),
    defaults=("vid.aivideox.site", ".aivideox.site", "img.aiphotofree.site", ".aiphotofree.site", ".dsfkjngkjndf.sbs"),
    strict=False,
)
SCHEDULE_DISPLAY_GMT_OFFSET_MINUTES = int(os.getenv("DLHD_SCHEDULE_DISPLAY_GMT_OFFSET_MINUTES", "0"))
EVENT_STALE_AFTER_MINUTES = int(os.getenv("DLHD_EVENT_STALE_AFTER_MINUTES", "360"))

COUNTRY_LABELS = {
    "br": "Brazil",
    "us": "United States",
    "gb": "United Kingdom",
    "es": "Spain",
    "fr": "France",
    "it": "Italy",
    "pt": "Portugal",
    "tr": "Turkey",
    "pl": "Poland",
    "ca": "Canada",
    "mx": "Mexico",
    "de": "Germany",
    "global": "Global / Regional",
}

COUNTRY_COLORS = {
    "br": "#166534",
    "us": "#1d4ed8",
    "gb": "#1e3a8a",
    "es": "#b45309",
    "fr": "#1e40af",
    "it": "#15803d",
    "pt": "#065f46",
    "tr": "#b91c1c",
    "pl": "#be123c",
    "ca": "#dc2626",
    "mx": "#047857",
    "de": "#111827",
    "global": "#4b5563",
}

VISIBLE_COUNTRY_CODES = [
    "br",
    "us",
    "gb",
    "es",
    "fr",
    "it",
    "pt",
    "tr",
    "pl",
    "ca",
    "mx",
    "de",
    "global",
]

FIXED_CATALOGS = [
    {"id": "channels_all", "name": "All Channels", "kind": "channels", "country_code": None},
    {"id": "live_all", "name": "All Live", "kind": "live", "country_code": None},
]

for country_code in VISIBLE_COUNTRY_CODES:
    label = COUNTRY_LABELS[country_code]
    FIXED_CATALOGS.append(
        {
            "id": f"channels_{country_code}",
            "name": f"{label} Streams",
            "kind": "channels",
            "country_code": country_code,
        }
    )

for country_code in VISIBLE_COUNTRY_CODES:
    label = COUNTRY_LABELS[country_code]
    FIXED_CATALOGS.append(
        {
            "id": f"live_{country_code}",
            "name": f"{label} Live",
            "kind": "live",
            "country_code": country_code,
        }
    )

CATALOGS_BY_ID = {catalog["id"]: catalog for catalog in FIXED_CATALOGS}
