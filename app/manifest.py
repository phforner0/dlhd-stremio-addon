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


def build_manifest(base_url: str, *, configured: bool = False) -> dict:
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
            for catalog in settings.FIXED_CATALOGS
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
