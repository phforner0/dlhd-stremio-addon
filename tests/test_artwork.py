from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.artwork import (
    CHANNEL_ARTWORK_CACHE,
    EVENT_ARTWORK_CACHE,
    EVENT_BADGE_SVG_CACHE,
    ArtworkResolution,
    _candidate_score,
    fetch_artwork_binary,
    resolve_channel_artwork,
    resolve_event_badge_svg,
    resolve_event_artwork,
)
from app.models import (
    CatalogChannel,
    ChannelInfo,
    LiveEvent,
    PageInfo,
    PlayerInfo,
    PrimaryPlayer,
    RelatedInfo,
    ScheduleChannelLink,
    SourceInfo,
    WrapperCatalog,
)


def _channel() -> CatalogChannel:
    return CatalogChannel(
        channel_id=81,
        name="ESPN Brasil",
        watch_url="https://dlstreams.top/watch.php?id=81",
        search_hint=None,
        group_letter=None,
        country_code="br",
        country_label="Brazil",
    )


def _wrapper(*, poster: str | None) -> WrapperCatalog:
    return WrapperCatalog(
        source=SourceInfo(input="https://dlstreams.top/watch.php?id=81", type="url"),
        channel=ChannelInfo(id=81, name="ESPN Brasil", heading="ESPN Brasil (ID 81)"),
        page=PageInfo(title="ESPN Brasil", description="Sports channel", canonicalUrl="https://dlstreams.top/watch.php?id=81", poster=poster),
        player=PlayerInfo(primary=PrimaryPlayer(label="primary", url=None), alternates=[]),
        related=RelatedInfo(label=None, channels=[]),
    )


def test_resolve_channel_artwork_ignores_placeholder_poster() -> None:
    channel = _channel()
    CHANNEL_ARTWORK_CACHE.delete(f"channel:{channel.channel_id}")

    resolution = resolve_channel_artwork(channel, lambda: _wrapper(poster="http://dlstreams.com/assets/logos/logo.png"))

    assert resolution == ArtworkResolution(None, None, "svg")


def test_resolve_channel_artwork_uses_real_upstream_poster() -> None:
    channel = _channel()
    CHANNEL_ARTWORK_CACHE.delete(f"channel:{channel.channel_id}")

    resolution = resolve_channel_artwork(channel, lambda: _wrapper(poster="https://dlstreams.com/assets/logos/espn-brasil.png"))

    assert resolution.poster_url == "https://dlstreams.com/assets/logos/espn-brasil.png"
    assert resolution.background_url == resolution.poster_url
    assert resolution.source == "watch_page"


def test_resolve_event_artwork_uses_best_matching_thesportsdb_result(monkeypatch) -> None:
    event = LiveEvent(
        meta_id="dlhd:live:event-1",
        title="England - League One : AFC Wimbledon vs Stockport County",
        time_text="20:00",
        day_label="Today",
        category="Football",
        channels=[ScheduleChannelLink(channel_id=81, name="ESPN Brasil", country_code="br", country_label="Brazil")],
        country_codes=["gb"],
        scheduled_at_utc=datetime(2026, 4, 15, 20, 0, tzinfo=timezone.utc),
    )
    EVENT_ARTWORK_CACHE.delete(f"event:{event.meta_id}")

    monkeypatch.setattr(
        "app.artwork._search_event_artwork",
        lambda query: [
            {
                "strEvent": "AFC Wimbledon vs Stockport County",
                "strSport": "Soccer",
                "strTimestamp": "2026-04-15T20:00:00",
                "strPoster": "https://r2.thesportsdb.com/images/media/event/poster/poster.jpg",
                "strThumb": "https://r2.thesportsdb.com/images/media/event/thumb/thumb.jpg",
                "strBanner": "https://r2.thesportsdb.com/images/media/event/banner/banner.jpg",
            },
            {
                "strEvent": "AFC Wimbledon vs Stockport County",
                "strSport": "Soccer",
                "strTimestamp": "2026-04-13T20:00:00",
                "strPoster": "https://r2.thesportsdb.com/images/media/event/poster/older.jpg",
            },
        ],
    )

    resolution = resolve_event_artwork(event)

    assert resolution.poster_url == "https://r2.thesportsdb.com/images/media/event/poster/poster.jpg"
    assert resolution.background_url == "https://r2.thesportsdb.com/images/media/event/banner/banner.jpg"
    assert resolution.source == "thesportsdb"


def test_event_candidate_score_prefers_team_and_league_match() -> None:
    event = LiveEvent(
        meta_id="dlhd:live:event-score",
        title="England - League One : AFC Wimbledon vs Stockport County",
        time_text="20:00",
        day_label="Today",
        category="Football",
        channels=[],
        country_codes=["gb"],
        scheduled_at_utc=datetime(2026, 4, 15, 20, 0, tzinfo=timezone.utc),
    )
    strong = {
        "strEvent": "AFC Wimbledon vs Stockport County",
        "strHomeTeam": "AFC Wimbledon",
        "strAwayTeam": "Stockport County",
        "strLeague": "England - League One",
        "strSport": "Soccer",
        "strTimestamp": "2026-04-15T20:00:00",
        "strPoster": "https://r2.thesportsdb.com/images/media/event/poster/poster.jpg",
    }
    weak = {
        "strEvent": "AFC Wimbledon vs Stockport County",
        "strHomeTeam": "AFC Wimbledon",
        "strAwayTeam": "Stockport County",
        "strLeague": "Club Friendlies",
        "strSport": "Soccer",
        "strTimestamp": "2026-04-13T20:00:00",
    }

    assert _candidate_score(event, event.title, strong) > _candidate_score(event, event.title, weak)


def test_resolve_event_artwork_falls_back_when_no_strong_match(monkeypatch) -> None:
    event = LiveEvent(
        meta_id="dlhd:live:event-2",
        title="Premier League / La Liga / Bundesliga League Matches (Football Season), May June 2026",
        time_text="18:00",
        day_label="Today",
        category="Upcoming Events",
        channels=[],
        country_codes=["global"],
        scheduled_at_utc=datetime(2026, 4, 15, 18, 0, tzinfo=timezone.utc),
    )
    EVENT_ARTWORK_CACHE.delete(f"event:{event.meta_id}")
    monkeypatch.setattr("app.artwork._search_event_artwork", lambda query: [])

    resolution = resolve_event_artwork(event)

    assert resolution == ArtworkResolution(None, None, "svg")


def test_resolve_event_badge_svg_builds_composed_svg_from_team_badges(monkeypatch) -> None:
    event = LiveEvent(
        meta_id="dlhd:live:event-3",
        title="England - League One : AFC Wimbledon vs Stockport County",
        time_text="20:00",
        day_label="Today",
        category="Football",
        channels=[],
        country_codes=["gb"],
        scheduled_at_utc=datetime(2026, 4, 15, 20, 0, tzinfo=timezone.utc),
    )
    EVENT_BADGE_SVG_CACHE.delete(f"event-badge:{event.meta_id}")

    monkeypatch.setattr(
        "app.artwork._search_team_artwork",
        lambda query: [
            {"strTeam": query, "strSport": "Soccer", "strBadge": f"https://r2.thesportsdb.com/images/media/team/badge/{query.replace(' ', '-').lower()}.png"}
        ],
    )
    monkeypatch.setattr(
        "app.artwork.fetch_artwork_binary",
        lambda url: (b"png-bytes", "image/png"),
    )

    svg = resolve_event_badge_svg(event)

    assert svg is not None
    assert "data:image/png;base64" in svg
    assert "AFC Wimbledon" in svg
    assert "Stockport County" in svg


def test_fetch_artwork_binary_rejects_non_image_content(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200
        url = "https://r2.thesportsdb.com/images/media/event/poster/poster.jpg"
        headers = {"Content-Type": "text/html; charset=utf-8"}
        content = b"<html></html>"

        def raise_for_status(self) -> None:
            return None

        def close(self) -> None:
            return None

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    monkeypatch.setattr("app.artwork.build_session", lambda: FakeSession())
    monkeypatch.setattr("app.artwork.guarded_get", lambda *args, **kwargs: FakeResponse())

    with pytest.raises(ValueError):
        fetch_artwork_binary("https://r2.thesportsdb.com/images/media/event/poster/poster.jpg")
