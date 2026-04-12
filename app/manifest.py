from __future__ import annotations

from app import settings


def _timezone_options() -> list[str]:
    options: list[str] = []
    for minutes in range(-12 * 60, 14 * 60 + 1, 30):
        sign = "+" if minutes >= 0 else "-"
        absolute = abs(minutes)
        hours = absolute // 60
        mins = absolute % 60
        options.append(f"{minutes}|GMT {sign}{hours:02d}:{mins:02d}")
    return options


def _country_options() -> list[str]:
    options = ["all|All Countries"]
    for country_code in settings.VISIBLE_COUNTRY_CODES:
        options.append(f"{country_code}|{settings.COUNTRY_LABELS[country_code]}")
    return options


def _catalogs_for_user(config: dict[str, str] | None) -> list[dict]:
    config = config or {}
    catalog_mode = config.get("catalogMode", "full").split("|", 1)[0]
    preferred_country = config.get("preferredCountryCode", "all").split("|", 1)[0]
    if catalog_mode != "focused" or preferred_country in {"", "all"}:
        return settings.FIXED_CATALOGS

    focused_ids = ["channels_all", "live_all"]
    if preferred_country in settings.COUNTRY_LABELS:
        focused_ids.extend([f"channels_{preferred_country}", f"live_{preferred_country}"])
    if preferred_country != "global":
        focused_ids.extend(["channels_global", "live_global"])

    seen_ids: set[str] = set()
    catalogs: list[dict] = []
    for catalog_id in focused_ids:
        catalog = settings.CATALOGS_BY_ID.get(catalog_id)
        if catalog is None or catalog_id in seen_ids:
            continue
        seen_ids.add(catalog_id)
        catalogs.append(catalog)
    return catalogs


def build_manifest(base_url: str, *, configured: bool = False, user_config: dict[str, str] | None = None) -> dict:
    catalogs = _catalogs_for_user(user_config)
    manifest = {
        "id": settings.ADDON_ID,
        "version": settings.ADDON_VERSION,
        "name": settings.ADDON_NAME,
        "description": settings.ADDON_DESCRIPTION,
        "resources": [
            "catalog",
            {"name": "meta", "types": ["tv"], "idPrefixes": ["dlhd:"]},
            {"name": "stream", "types": ["tv"], "idPrefixes": ["dlhd:"]},
        ],
        "types": ["tv"],
        "idPrefixes": ["dlhd:"],
        "catalogs": [
            {
                "type": "tv",
                "id": catalog["id"],
                "name": catalog["name"],
                "extra": [
                    {"name": "search", "isRequired": False},
                    {"name": "skip", "isRequired": False},
                ],
            }
            for catalog in catalogs
        ],
        "logo": f"{base_url}/assets/logo.svg",
        "background": f"{base_url}/assets/background.svg",
        "behaviorHints": {
            "adult": False,
            "p2p": False,
            "configurable": True,
        },
        "config": [
            {
                "key": "scheduleOffsetMin",
                "type": "select",
                "default": str(settings.SCHEDULE_DISPLAY_GMT_OFFSET_MINUTES),
                "title": "Schedule Timezone",
                "options": _timezone_options(),
            },
            {
                "key": "catalogMode",
                "type": "select",
                "default": "full",
                "title": "Catalog Mode",
                "options": [
                    "full|All catalogs",
                    "focused|Only all + one country + global",
                ],
            },
            {
                "key": "preferredCountryCode",
                "type": "select",
                "default": "all",
                "title": "Preferred Country",
                "options": _country_options(),
            },
            {
                "key": "channelStreamResults",
                "type": "select",
                "default": str(settings.CHANNEL_STREAM_MAX_RESULTS),
                "title": "Channel Stream Options",
                "options": ["1|Best only", "2|Two options"],
            },
            {
                "key": "liveStreamResults",
                "type": "select",
                "default": str(settings.LIVE_STREAM_MAX_RESULTS),
                "title": "Live Stream Options",
                "options": ["1|Best only", "2|Two options", "4|More options"],
            },
            {
                "key": "eventStaleAfterMinutes",
                "type": "select",
                "default": str(settings.EVENT_STALE_AFTER_MINUTES),
                "title": "Hide Finished Events After",
                "options": ["120|2 hours", "360|6 hours", "720|12 hours"],
            }
        ],
    }

    if configured:
        manifest = dict(manifest)
        behavior_hints = dict(manifest["behaviorHints"])
        behavior_hints.pop("configurable", None)
        manifest["behaviorHints"] = behavior_hints
        manifest.pop("config", None)

    return manifest
