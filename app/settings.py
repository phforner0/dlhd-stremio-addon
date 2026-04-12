from __future__ import annotations

import os

BASE_SITE_URL = os.getenv("DLHD_BASE_URL", "https://dlstreams.top").rstrip("/")
ADDON_ID = os.getenv("DLHD_ADDON_ID", "com.dlhd.stremio")
ADDON_NAME = os.getenv("DLHD_ADDON_NAME", "DLHD Streams")
ADDON_VERSION = os.getenv("DLHD_ADDON_VERSION", "0.1.3")
ADDON_DESCRIPTION = os.getenv(
    "DLHD_ADDON_DESCRIPTION",
    "Scraped live channels and schedule grouped by country for Stremio.",
)

HTTP_TIMEOUT_SECONDS = int(os.getenv("DLHD_HTTP_TIMEOUT", "20"))
PLAYWRIGHT_WAIT_SECONDS = int(os.getenv("DLHD_PLAYWRIGHT_WAIT", "6"))
PLAYWRIGHT_TIMEOUT_MS = int(os.getenv("DLHD_PLAYWRIGHT_TIMEOUT_MS", "20000"))
PLAYWRIGHT_MAX_CONCURRENCY = int(os.getenv("DLHD_PLAYWRIGHT_MAX_CONCURRENCY", "4"))
CHANNEL_STREAM_MAX_RESULTS = int(os.getenv("DLHD_CHANNEL_STREAM_MAX_RESULTS", "2"))
CHANNEL_STREAM_MAX_ATTEMPTS = int(os.getenv("DLHD_CHANNEL_STREAM_MAX_ATTEMPTS", "3"))
LIVE_STREAM_MAX_WORKERS = int(
    os.getenv("DLHD_LIVE_STREAM_MAX_WORKERS", str(min(PLAYWRIGHT_MAX_CONCURRENCY, 4)))
)
LIVE_STREAM_MAX_RESULTS = int(os.getenv("DLHD_LIVE_STREAM_MAX_RESULTS", "4"))
LIVE_STREAM_MAX_ATTEMPTS = int(os.getenv("DLHD_LIVE_STREAM_MAX_ATTEMPTS", "6"))
CATALOG_PAGE_SIZE = int(os.getenv("DLHD_CATALOG_PAGE_SIZE", "100"))

CHANNELS_CACHE_TTL_SECONDS = int(os.getenv("DLHD_CHANNELS_CACHE_TTL", str(12 * 60 * 60)))
SCHEDULE_CACHE_TTL_SECONDS = int(os.getenv("DLHD_SCHEDULE_CACHE_TTL", "120"))
WATCH_CACHE_TTL_SECONDS = int(os.getenv("DLHD_WATCH_CACHE_TTL", str(30 * 60)))
STREAM_CACHE_TTL_SECONDS = int(os.getenv("DLHD_STREAM_CACHE_TTL", "120"))
LIVE_CHANNEL_CACHE_TTL_SECONDS = int(os.getenv("DLHD_LIVE_CHANNEL_CACHE_TTL", "120"))
FAILED_STREAM_CACHE_TTL_SECONDS = int(os.getenv("DLHD_FAILED_STREAM_CACHE_TTL", "30"))
HLS_PLAYLIST_CACHE_TTL_SECONDS = int(os.getenv("DLHD_HLS_PLAYLIST_CACHE_TTL", "15"))

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
