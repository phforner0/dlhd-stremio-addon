from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.models import ChannelInfo, PageInfo, PlayerInfo, PrimaryPlayer, RelatedInfo, SourceInfo, WrapperCatalog


def test_catalog_invalid_skip_returns_400() -> None:
    client = TestClient(app)

    response = client.get("/catalog/tv/channels_all/skip=abc.json")

    assert response.status_code == 400
    assert response.json()["detail"] == "invalid skip"


def test_catalog_negative_skip_clamps_to_zero(monkeypatch) -> None:
    client = TestClient(app)
    calls: dict[str, object] = {}

    def fake_filter_channels(channels, *, country_code, search, skip):
        calls["skip"] = skip
        return []

    monkeypatch.setattr("app.main.get_channels", lambda: [])
    monkeypatch.setattr("app.main.filter_channels", fake_filter_channels)

    response = client.get("/catalog/tv/channels_all/skip=-5.json")

    assert response.status_code == 200
    assert calls["skip"] == 0


def test_meta_unknown_channel_maps_watch_404_to_not_found(monkeypatch) -> None:
    client = TestClient(app)

    class FakeWatchError(Exception):
        def __init__(self):
            self.status_code = 404

    monkeypatch.setattr("app.main.get_channel_index", lambda: {})
    monkeypatch.setattr("app.main.watch_cache.get", lambda key: None)
    monkeypatch.setattr("app.main.get_wrapper", lambda channel_id: (_ for _ in ()).throw(FakeWatchError()))
    monkeypatch.setattr("app.main.WatchFetchError", FakeWatchError)

    response = client.get("/meta/tv/dlhd:ch:999999.json")

    assert response.status_code == 404


def test_stream_unknown_channel_maps_watch_error_to_not_found(monkeypatch) -> None:
    client = TestClient(app)

    class FakeWatchError(Exception):
        def __init__(self):
            self.status_code = 404

    monkeypatch.setattr("app.main.get_cached_channel", lambda channel_id: None)
    monkeypatch.setattr("app.main.get_wrapper", lambda channel_id: (_ for _ in ()).throw(FakeWatchError()))
    monkeypatch.setattr("app.main.WatchFetchError", FakeWatchError)

    response = client.get("/stream/tv/dlhd:ch:999999.json")

    assert response.status_code == 404


def test_meta_channel_falls_back_to_wrapper_when_missing_from_index(monkeypatch) -> None:
    client = TestClient(app)
    wrapper = WrapperCatalog(
        source=SourceInfo(input="https://dlstreams.top/watch.php?id=81", type="url"),
        channel=ChannelInfo(id=81, name="ESPN Brasil", heading="ESPN Brasil (ID 81)"),
        page=PageInfo(title="ESPN Brasil", description="Sports channel", canonicalUrl="https://dlstreams.top/watch.php?id=81", poster=None),
        player=PlayerInfo(primary=PrimaryPlayer(label="primary", url=None), alternates=[]),
        related=RelatedInfo(label=None, channels=[]),
    )

    monkeypatch.setattr("app.main.get_channel_index", lambda: {})
    monkeypatch.setattr("app.main.watch_cache.get", lambda key: None)
    monkeypatch.setattr("app.main._wrapper_or_http_error", lambda channel_id: wrapper)

    response = client.get("/meta/tv/dlhd:ch:81.json")

    assert response.status_code == 200
    assert response.json()["meta"]["name"] == "ESPN Brasil"
    assert "/assets/background/" in response.json()["meta"]["background"]


def test_invalid_config_token_falls_back_to_default_manifest() -> None:
    client = TestClient(app)

    response = client.get("/cfg-not-a-real-token/manifest.json")

    assert response.status_code == 200
    assert response.json()["version"] == "0.1.3"
    assert response.json()["behaviorHints"]["configurable"] is True
