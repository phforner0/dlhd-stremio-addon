from __future__ import annotations

from app import settings


def build_manifest(base_url: str) -> dict:
    return {
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
        },
    }
