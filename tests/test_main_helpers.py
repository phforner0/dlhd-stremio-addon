from __future__ import annotations

from datetime import datetime, timezone
from starlette.requests import Request

from app import settings
from app.main import (
    _build_channel_streams,
    _channel_player_cache_key,
    _encode_user_config,
    _effective_country_filter,
    _event_display_values,
    _hls_playlist_rejection_reason,
    _is_hls_playlist_candidate,
    _looks_like_hls_playlist,
    _ordered_live_channels,
    _parse_user_config,
    _resolve_live_channel_stream,
    channel_player_cache,
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


def test_hls_playlist_rejection_detects_obfuscated_workers() -> None:
    body = "#EXTM3U\n# uploader-meta: version=3.1.95; mode=s3; delivery=workers\nsegment.js\n"

    assert _hls_playlist_rejection_reason(body) == "obfuscated_worker_playlist"


def test_hls_playlist_candidate_accepts_css_text_playlist_sources() -> None:
    assert _is_hls_playlist_candidate("https://example.test/mono.css", "text/txt") is True


def test_parse_user_config_accepts_json_path_segment() -> None:
    config = _parse_user_config('{"scheduleOffsetMin":"-180"}')

    assert config == {"scheduleOffsetMin": "-180"}


def test_parse_user_config_accepts_base64url_token() -> None:
    token = _encode_user_config({"scheduleOffsetMin": "-180", "catalogMode": "focused"})

    config = _parse_user_config(token)

    assert config == {"scheduleOffsetMin": "-180", "catalogMode": "focused"}


def test_effective_country_filter_keeps_all_catalogs_unfiltered() -> None:
    catalog_def = {"country_code": None}

    country_code = _effective_country_filter(catalog_def, {"catalogMode": "focused", "preferredCountryCode": "br"})

    assert country_code is None


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

    ordered = _ordered_live_channels(event, {})

    assert [channel.channel_id for channel in ordered] == [88, 138]
    live_channel_stream_cache.delete("live-channel:138")


def test_resolve_live_channel_stream_keeps_addon_name_and_descriptive_label() -> None:
    from app import main as main_module

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

    original_validator = main_module._valid_hls_stream
    main_module._valid_hls_stream = lambda manifest_url, referer: True

    stream = _resolve_live_channel_stream(event, linked_channel, request)

    assert stream is not None
    assert stream["name"] == settings.ADDON_NAME
    assert "Premier Brasil" in stream["description"]
    assert "Brazil" in stream["description"]
    assert "Athletico-PR vs Chapecoense" in stream["description"]
    live_channel_stream_cache.delete("live-channel:88")
    main_module._valid_hls_stream = original_validator


def test_event_display_values_use_user_offset() -> None:
    event = LiveEvent(
        meta_id="dlhd:live:test",
        title="Test Event",
        time_text="14:00",
        day_label="Sunday 12th April 2026 - Schedule Time UK GMT",
        category="Soccer",
        country_codes=["br"],
        scheduled_at_utc=datetime(2026, 4, 12, 14, 0, tzinfo=timezone.utc),
    )

    day_label, time_text = _event_display_values(event, {"scheduleOffsetMin": "-180"})

    assert day_label == "Sunday 12th April 2026 - Schedule Time GMT -03:00"
    assert time_text == "11:00"


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
    monkeypatch.setattr("app.main._valid_hls_stream", lambda manifest_url, referer: True)

    cache_key = f"stream:channel:{channel.channel_id}:http://127.0.0.1:7000:default"
    stream_cache.delete(cache_key)

    streams = _build_channel_streams(channel, request, {})

    assert len(streams) == 1
    assert streams[0]["name"] == settings.ADDON_NAME
    assert "ESPN Brasil" in streams[0]["description"]
    assert "Brazil" in streams[0]["description"]
    assert "Player 1" in streams[0]["description"]
    stream_cache.delete(cache_key)


def test_build_channel_streams_skips_invalid_hls_manifest(monkeypatch) -> None:
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
            alternates=[],
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
    monkeypatch.setattr("app.main._valid_hls_stream", lambda manifest_url, referer: False)

    cache_key = f"stream:channel:{channel.channel_id}:http://127.0.0.1:7000:{'channelStreamResults=1'}"
    stream_cache.delete(cache_key)

    streams = _build_channel_streams(channel, request, {"channelStreamResults": "1"})

    assert streams == []
    stream_cache.delete(cache_key)


def test_build_channel_streams_keeps_third_player_available_under_low_attempt_limit(monkeypatch) -> None:
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
            primary=PrimaryPlayer(label="primary", url="https://example.test/stream"),
            alternates=[
                AlternatePlayer(label="Player 1", url="https://example.test/stream", active=True),
                AlternatePlayer(label="Player 2", url="https://example.test/cast", active=False),
                AlternatePlayer(label="Player 3", url="https://example.test/watch", active=False),
            ],
        ),
        related=RelatedInfo(label=None),
    )
    resolutions = {
        "https://example.test/stream": PlayerResolution(
            label="Player 1",
            player_page_url="https://example.test/stream",
            manifests=[
                ManifestResult(
                    url="https://example.test/stream.m3u8",
                    player_type="hls",
                    found_at_url="https://example.test/stream",
                    source="network",
                )
            ],
        ),
        "https://example.test/cast": PlayerResolution(
            label="Player 2",
            player_page_url="https://example.test/cast",
            manifests=[
                ManifestResult(
                    url="https://example.test/cast.m3u8",
                    player_type="hls",
                    found_at_url="https://example.test/cast",
                    source="network",
                )
            ],
        ),
        "https://example.test/watch": PlayerResolution(
            label="Player 3",
            player_page_url="https://example.test/watch",
            manifests=[
                ManifestResult(
                    url="https://example.test/watch.m3u8",
                    player_type="hls",
                    found_at_url="https://example.test/watch",
                    source="network",
                )
            ],
        ),
    }
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
    monkeypatch.setattr("app.main.resolver.resolve_player", lambda label, player_url: resolutions[player_url])
    monkeypatch.setattr(
        "app.main._valid_hls_stream",
        lambda manifest_url, referer: manifest_url == "https://example.test/watch.m3u8",
    )
    monkeypatch.setattr(settings, "CHANNEL_STREAM_MAX_ATTEMPTS", 2)

    cache_key = f"stream:channel:{channel.channel_id}:http://127.0.0.1:7000:default"
    stream_cache.delete(cache_key)

    streams = _build_channel_streams(channel, request, {})

    assert len(streams) == 1
    assert streams[0]["description"].endswith("Player 3 | HLS")
    stream_cache.delete(cache_key)


def test_build_channel_streams_falls_back_to_remaining_players_after_initial_batch(monkeypatch) -> None:
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
            primary=PrimaryPlayer(label="primary", url="https://example.test/player-1"),
            alternates=[
                AlternatePlayer(label="Player 1", url="https://example.test/player-1", active=True),
                AlternatePlayer(label="Player 2", url="https://example.test/player-2", active=False),
                AlternatePlayer(label="Player 3", url="https://example.test/player-3", active=False),
                AlternatePlayer(label="Player 4", url="https://example.test/player-4", active=False),
            ],
        ),
        related=RelatedInfo(label=None),
    )
    resolutions = {
        player_url: PlayerResolution(label=label, player_page_url=player_url, manifests=[])
        for label, player_url in (
            ("Player 1", "https://example.test/player-1"),
            ("Player 2", "https://example.test/player-2"),
            ("Player 3", "https://example.test/player-3"),
        )
    }
    resolutions["https://example.test/player-4"] = PlayerResolution(
        label="Player 4",
        player_page_url="https://example.test/player-4",
        manifests=[
            ManifestResult(
                url="https://example.test/player-4.m3u8",
                player_type="hls",
                found_at_url="https://example.test/player-4",
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
    monkeypatch.setattr("app.main.resolver.resolve_player", lambda label, player_url: resolutions[player_url])
    monkeypatch.setattr("app.main._valid_hls_stream", lambda manifest_url, referer: True)
    monkeypatch.setattr(settings, "CHANNEL_STREAM_MAX_ATTEMPTS", 2)

    cache_key = f"stream:channel:{channel.channel_id}:http://127.0.0.1:7000"
    stream_cache.delete(cache_key)
    channel_player_cache.delete(_channel_player_cache_key(channel.channel_id))

    streams = _build_channel_streams(channel, request, {})

    assert len(streams) == 1
    assert streams[0]["description"].endswith("Player 4 | HLS")
    assert channel_player_cache.get(_channel_player_cache_key(channel.channel_id)) == "https://example.test/player-4"
    stream_cache.delete(cache_key)
    channel_player_cache.delete(_channel_player_cache_key(channel.channel_id))


def test_build_channel_streams_prefers_last_successful_player(monkeypatch) -> None:
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
            primary=PrimaryPlayer(label="primary", url="https://example.test/player-1"),
            alternates=[
                AlternatePlayer(label="Player 1", url="https://example.test/player-1", active=True),
                AlternatePlayer(label="Player 2", url="https://example.test/player-2", active=False),
            ],
        ),
        related=RelatedInfo(label=None),
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
    call_order: list[str] = []
    resolutions = {
        "https://example.test/player-1": PlayerResolution(label="Player 1", player_page_url="https://example.test/player-1", manifests=[]),
        "https://example.test/player-2": PlayerResolution(
            label="Player 2",
            player_page_url="https://example.test/player-2",
            manifests=[
                ManifestResult(
                    url="https://example.test/player-2.m3u8",
                    player_type="hls",
                    found_at_url="https://example.test/player-2",
                    source="network",
                )
            ],
        ),
    }

    def resolve_player(label, player_url):
        call_order.append(player_url)
        return resolutions[player_url]

    monkeypatch.setattr("app.main.get_wrapper", lambda channel_id: wrapper)
    monkeypatch.setattr("app.main.resolver.resolve_player", resolve_player)
    monkeypatch.setattr("app.main._valid_hls_stream", lambda manifest_url, referer: True)
    channel_player_cache.set(_channel_player_cache_key(channel.channel_id), "https://example.test/player-2", 60)

    cache_key = f"stream:channel:{channel.channel_id}:http://127.0.0.1:7000:{'channelStreamResults=1'}"
    stream_cache.delete(cache_key)

    streams = _build_channel_streams(channel, request, {"channelStreamResults": "1"})

    assert len(streams) == 1
    assert call_order[0] == "https://example.test/player-2"
    stream_cache.delete(cache_key)
    channel_player_cache.delete(_channel_player_cache_key(channel.channel_id))
