from __future__ import annotations

from datetime import datetime, timezone

from starlette.requests import Request

from app.main import _channel_preview, _event_preview
from app.models import CatalogChannel, LiveEvent, ScheduleChannelLink
from app.posters import render_svg_poster


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

    assert payload["background"] == payload["poster"]
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

    assert payload["background"] == payload["poster"]
    assert payload["releaseInfo"]
    assert "Football" in payload["description"]
    assert "Channels: ESPN Brasil, Premiere Brasil" in payload["description"]
    assert "Brazil" in payload["description"]
