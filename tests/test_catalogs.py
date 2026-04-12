from app import settings


def test_fixed_catalog_count() -> None:
    assert len(settings.FIXED_CATALOGS) == 28


def test_expected_catalogs_exist() -> None:
    ids = {catalog["id"] for catalog in settings.FIXED_CATALOGS}
    assert "channels_br" in ids
    assert "channels_global" in ids
    assert "live_us" in ids
    assert "live_global" in ids
