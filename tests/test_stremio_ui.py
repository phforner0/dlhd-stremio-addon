from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.artwork import ArtworkResolution
from starlette.requests import Request

from app.main import _channel_preview, _event_preview, app
from app.models import CatalogChannel, LiveEvent, ScheduleChannelLink
from app.posters import render_svg_background, render_svg_poster


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "scheme": "https",
            "server": ("example.test", 443),
            "client": ("127.0.0.1", 12345),
            "path": "/",
            "root_path": "",
            "query_string": b"",
            "headers": [],
        }
    )


def test_render_svg_poster_uses_svg_text_instead_of_foreignobject() -> None:
    svg = render_svg_poster("ESPN Brasil", "Brazil | Channel 81 | Sports coverage", "br")

    assert "<foreignObject" not in svg
    assert "<tspan" in svg
    assert "ESPN Brasil" in svg


def test_render_svg_background_uses_landscape_dimensions() -> None:
    svg = render_svg_background("Barcelona vs Real Madrid", "Today | 20:00 | Football", "es")

    assert 'width="1600"' in svg
    assert 'height="900"' in svg
    assert "<foreignObject" not in svg


def test_channel_preview_has_background_release_info_and_richer_description() -> None:
    request = _request()
    channel = CatalogChannel(
        channel_id=81,
        name="ESPN Brasil",
        watch_url="https://dlstreams.top/watch.php?id=81",
        search_hint=None,
        group_letter=None,
        country_code="br",
        country_label="Brazil",
    )

    payload = _channel_preview(channel, request)

    assert payload["background"].replace("/assets/background/", "/assets/poster/") != payload["background"]
    assert payload["releaseInfo"] == "Brazil"
    assert payload["description"] == "Brazil live TV channel | Channel 81"


def test_event_preview_surfaces_schedule_countries_and_channels() -> None:
    request = _request()
    event = LiveEvent(
        meta_id="dlhd:live:test-1",
        title="Barcelona vs Real Madrid",
        time_text="20:00",
        day_label="Today",
        category="Football",
        channels=[
            ScheduleChannelLink(channel_id=81, name="ESPN Brasil", country_code="br", country_label="Brazil"),
            ScheduleChannelLink(channel_id=89, name="Premiere Brasil", country_code="br", country_label="Brazil"),
        ],
        country_codes=["br", "global"],
        ordinal=1,
        scheduled_at_utc=datetime(2026, 4, 15, 23, 0, tzinfo=timezone.utc),
    )

    payload = _event_preview(event, request, {"scheduleOffsetMin": "-180"})

    assert "/assets/background/" in payload["background"]
    assert "/assets/poster/" in payload["poster"]
    assert payload["releaseInfo"]
    assert "Football" in payload["description"]
    assert "Channels: ESPN Brasil, Premiere Brasil" in payload["description"]
    assert "Brazil" in payload["description"]


def test_dynamic_poster_route_can_return_remote_image(monkeypatch) -> None:
    client = TestClient(app)
    channel = CatalogChannel(
        channel_id=81,
        name="ESPN Brasil",
        watch_url="https://dlstreams.top/watch.php?id=81",
        search_hint=None,
        group_letter=None,
        country_code="br",
        country_label="Brazil",
    )
    monkeypatch.setattr("app.main.get_channel_index", lambda: {81: channel})
    monkeypatch.setattr(
        "app.main.resolve_channel_artwork",
        lambda current_channel, wrapper_fetcher: ArtworkResolution(
            poster_url="https://dlstreams.com/assets/logos/espn-brasil.png",
            background_url="https://dlstreams.com/assets/logos/espn-brasil.png",
            source="watch_page",
        ),
    )
    monkeypatch.setattr("app.main.fetch_artwork_binary", lambda url: (b"png-bytes", "image/png"))

    response = client.get("/assets/poster/dlhd:ch:81")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == b"png-bytes"


def test_dynamic_background_route_prefers_background_image(monkeypatch) -> None:
    client = TestClient(app)
    channel = CatalogChannel(
        channel_id=81,
        name="ESPN Brasil",
        watch_url="https://dlstreams.top/watch.php?id=81",
        search_hint=None,
        group_letter=None,
        country_code="br",
        country_label="Brazil",
    )
    monkeypatch.setattr("app.main.get_channel_index", lambda: {81: channel})
    monkeypatch.setattr(
        "app.main.resolve_channel_artwork",
        lambda current_channel, wrapper_fetcher: ArtworkResolution(
            poster_url="https://dlstreams.com/assets/logos/espn-brasil.png",
            background_url="https://cdn.example.test/espn-background.jpg",
            source="watch_page",
        ),
    )
    monkeypatch.setattr("app.main.fetch_artwork_binary", lambda url: (url.encode("utf-8"), "image/jpeg"))

    response = client.get("/assets/background/dlhd:ch:81")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert b"espn-background.jpg" in response.content


def test_svg_poster_compatibility_route_still_returns_svg(monkeypatch) -> None:
    client = TestClient(app)
    channel = CatalogChannel(
        channel_id=81,
        name="ESPN Brasil",
        watch_url="https://dlstreams.top/watch.php?id=81",
        search_hint=None,
        group_letter=None,
        country_code="br",
        country_label="Brazil",
    )
    monkeypatch.setattr("app.main.get_channel_index", lambda: {81: channel})
    monkeypatch.setattr("app.main.watch_cache.get", lambda key: None)

    response = client.get("/assets/poster/dlhd:ch:81.svg")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert "<svg" in response.text


def test_svg_background_route_returns_landscape_svg(monkeypatch) -> None:
    client = TestClient(app)
    event = LiveEvent(
        meta_id="dlhd:live:test-bg",
        title="Barcelona vs Real Madrid",
        time_text="20:00",
        day_label="Today",
        category="Football",
        channels=[],
        country_codes=["es"],
        ordinal=1,
        scheduled_at_utc=datetime(2026, 4, 15, 23, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr("app.main._find_event", lambda meta_id: event)
    monkeypatch.setattr("app.main.resolve_event_badge_svg", lambda current_event, aspect="poster": None)

    response = client.get("/assets/background/dlhd:live:test-bg.svg")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert 'viewBox="0 0 1600 900"' in response.text


def test_dynamic_event_poster_route_can_fall_back_to_composed_badge_svg(monkeypatch) -> None:
    client = TestClient(app)
    event = LiveEvent(
        meta_id="dlhd:live:test-2",
        title="Barcelona vs Real Madrid",
        time_text="20:00",
        day_label="Today",
        category="Football",
        channels=[],
        country_codes=["es"],
        ordinal=1,
        scheduled_at_utc=datetime(2026, 4, 15, 23, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr("app.main._find_event", lambda meta_id: event)
    monkeypatch.setattr("app.main.resolve_event_artwork", lambda current_event: ArtworkResolution(None, None, "svg"))
    monkeypatch.setattr("app.main.resolve_event_badge_svg", lambda current_event, aspect="poster": "<svg><text>badge-fallback</text></svg>")

    response = client.get("/assets/poster/dlhd:live:test-2")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert "badge-fallback" in response.text
