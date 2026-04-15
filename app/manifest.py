from __future__ import annotations

from app import settings
from app.user_config import catalog_mode, manifest_config_fields, preferred_country_code


def _catalogs_for_user(config: dict[str, str] | None) -> list[dict]:
    config = config or {}
    selected_catalog_mode = catalog_mode(config)
    preferred_country = preferred_country_code(config)
    if selected_catalog_mode != "focused" or preferred_country is None:
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
        "config": manifest_config_fields(),
    }

    if configured:
        manifest = dict(manifest)
        behavior_hints = dict(manifest["behaviorHints"])
        behavior_hints.pop("configurable", None)
        manifest["behaviorHints"] = behavior_hints
        manifest.pop("config", None)

    return manifest
