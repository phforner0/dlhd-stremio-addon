from __future__ import annotations

from starlette.requests import Request

from app import settings
from app.main import (
    _build_channel_streams,
    _looks_like_hls_playlist,
    _ordered_live_channels,
    _resolve_live_channel_stream,
    live_channel_stream_cache,
    stream_cache,
)
from app.models import (
    AlternatePlayer,
    CatalogChannel,
    ChannelInfo,
    LiveEvent,
    ManifestResult,
    PageInfo,
    PlayerInfo,
    PlayerResolution,
    PrimaryPlayer,
    RelatedInfo,
    ScheduleChannelLink,
    SourceInfo,
    WrapperCatalog,
)


def test_hls_playlist_detection_accepts_extm3u() -> None:
    body = "#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:5.0,\nsegment.ts\n"

    assert _looks_like_hls_playlist(body) is True


def test_hls_playlist_detection_rejects_non_playlist_text() -> None:
    body = "console.log('not a playlist');"

    assert _looks_like_hls_playlist(body) is False


def test_ordered_live_channels_prefers_event_country_before_cached_global() -> None:
    event = LiveEvent(
        meta_id="dlhd:live:test",
        title="Test Event",
        time_text="14:00",
        day_label="Sunday",
        category="Soccer",
        channels=[
            ScheduleChannelLink(138, "Match Football 3 Russia", "global", "Global / Regional"),
            ScheduleChannelLink(88, "Premier Brasil", "br", "Brazil"),
        ],
        country_codes=["br"],
    )
    live_channel_stream_cache.set(
        "live-channel:138",
        {"url": "https://example.test/global.m3u8", "referer": "https://example.test", "player_type": "HLS"},
        60,
    )

    ordered = _ordered_live_channels(event)

    assert [channel.channel_id for channel in ordered] == [88, 138]
    live_channel_stream_cache.delete("live-channel:138")


def test_resolve_live_channel_stream_keeps_addon_name_and_descriptive_label() -> None:
    linked_channel = ScheduleChannelLink(88, "Premier Brasil", "br", "Brazil")
    event = LiveEvent(
        meta_id="dlhd:live:test",
        title="Brazil - Brasileirão : Athletico-PR vs Chapecoense",
        time_text="14:00",
        day_label="Sunday",
        category="Soccer",
        channels=[linked_channel],
        country_codes=["br"],
    )
    live_channel_stream_cache.set(
        "live-channel:88",
        {"url": "https://example.test/live.m3u8", "referer": "https://example.test", "player_type": "HLS"},
        60,
    )
    request = Request(
        {
            "type": "http",
            "scheme": "http",
            "server": ("127.0.0.1", 7000),
            "client": ("127.0.0.1", 12345),
            "path": "/",
            "root_path": "",
            "query_string": b"",
            "headers": [],
        }
    )

    stream = _resolve_live_channel_stream(event, linked_channel, request)

    assert stream is not None
    assert stream["name"] == settings.ADDON_NAME
    assert "Premier Brasil" in stream["description"]
    assert "Brazil" in stream["description"]
    assert "Athletico-PR vs Chapecoense" in stream["description"]
    live_channel_stream_cache.delete("live-channel:88")


def test_build_channel_streams_keeps_addon_name_and_descriptive_label(monkeypatch) -> None:
    channel = CatalogChannel(
        channel_id=81,
        name="ESPN Brasil",
        watch_url="https://example.test/watch.php?id=81",
        search_hint=None,
        group_letter=None,
        country_code="br",
        country_label="Brazil",
    )
    wrapper = WrapperCatalog(
        source=SourceInfo(input=channel.watch_url, type="url"),
        channel=ChannelInfo(id=81, name="ESPN Brasil", heading="ESPN Brasil"),
        page=PageInfo(title="ESPN Brasil", description="Sports", canonicalUrl=channel.watch_url, poster=None),
        player=PlayerInfo(
            primary=PrimaryPlayer(label="primary", url="https://example.test/player"),
            alternates=[AlternatePlayer(label="Player 1", url="https://example.test/player", active=True)],
        ),
        related=RelatedInfo(label=None),
    )
    resolution = PlayerResolution(
        label="Player 1",
        player_page_url="https://example.test/player",
        manifests=[
            ManifestResult(
                url="https://example.test/channel.m3u8",
                player_type="hls",
                found_at_url="https://example.test/ref",
                source="network",
            )
        ],
    )
    request = Request(
        {
            "type": "http",
            "scheme": "http",
            "server": ("127.0.0.1", 7000),
            "client": ("127.0.0.1", 12345),
            "path": "/",
            "root_path": "",
            "query_string": b"",
            "headers": [],
        }
    )

    monkeypatch.setattr("app.main.get_wrapper", lambda channel_id: wrapper)
    monkeypatch.setattr("app.main.resolver.resolve_player", lambda label, player_url: resolution)

    cache_key = f"stream:channel:{channel.channel_id}:http://127.0.0.1:7000"
    stream_cache.delete(cache_key)

    streams = _build_channel_streams(channel, request)

    assert len(streams) == 1
    assert streams[0]["name"] == settings.ADDON_NAME
    assert "ESPN Brasil" in streams[0]["description"]
    assert "Brazil" in streams[0]["description"]
    assert "Player 1" in streams[0]["description"]
    stream_cache.delete(cache_key)
