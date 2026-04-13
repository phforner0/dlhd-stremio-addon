from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


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
