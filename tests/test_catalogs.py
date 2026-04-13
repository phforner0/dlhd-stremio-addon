from app.manifest import build_manifest
from app import settings


def test_fixed_catalog_count() -> None:
    assert len(settings.FIXED_CATALOGS) == 28


def test_expected_catalogs_exist() -> None:
    ids = {catalog["id"] for catalog in settings.FIXED_CATALOGS}
    assert "channels_br" in ids
    assert "channels_global" in ids
    assert "live_us" in ids
    assert "live_global" in ids


def test_manifest_exposes_config_when_not_configured() -> None:
    manifest = build_manifest("https://example.test")

    assert manifest["behaviorHints"]["configurable"] is True
    assert manifest["config"]


def test_manifest_hides_config_after_configuration() -> None:
    manifest = build_manifest("https://example.test", configured=True)

    assert "config" not in manifest
    assert "configurable" not in manifest["behaviorHints"]


def test_focused_manifest_keeps_all_catalogs_present() -> None:
    manifest = build_manifest(
        "https://example.test",
        configured=True,
        user_config={"catalogMode": "focused", "preferredCountryCode": "br"},
    )

    ids = [catalog["id"] for catalog in manifest["catalogs"]]
    assert ids == [
        "channels_all",
        "live_all",
        "channels_br",
        "live_br",
        "channels_global",
        "live_global",
    ]
