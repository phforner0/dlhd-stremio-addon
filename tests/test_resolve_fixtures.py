from __future__ import annotations

from pathlib import Path

from app.main import _extract_hls_probe_targets, _looks_like_hls_playlist
from app.resolve.player import _extract_embed_proxy_manifest, _extract_embed_proxy_manifest_from_url
from app.resolve.providers import bootstrap_parse_results, matching_bootstrap_strategy_names, should_attempt_bootstrap_fetch
from app.scrape.watch import parse_wrapper


FIXTURES = Path(__file__).parent / "fixtures"


class _FakeResponse:
    def __init__(self, *, text: str = "", json_payload=None, status_code: int = 200) -> None:
        self.text = text
        self._json_payload = json_payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._json_payload


class _FakeSession:
    def __init__(self, html_by_url: dict[str, str]) -> None:
        self.headers: dict[str, str] = {}
        self._html_by_url = html_by_url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def get(self, url: str, params=None, timeout: int = 10):
        if url in self._html_by_url:
            return _FakeResponse(text=self._html_by_url[url])
        if url.endswith("/status"):
            return _FakeResponse(json_payload={"success": True})
        if url.endswith("/server_lookup"):
            channel_id = (params or {}).get("channel_id")
            server_key = "zeko" if channel_id == "premium81" else "x4"
            return _FakeResponse(json_payload={"server_key": server_key})
        if "/proxy/" in url and url.endswith("/mono.css"):
            return _FakeResponse(text="#EXTM3U\n#EXTINF:4.0,\nsegment.ts\n")
        raise AssertionError(f"unexpected url: {url}")


def _fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_wrapper_matches_watch_fixture() -> None:
    wrapper = parse_wrapper(_fixture_text("watch_page_81.html"), "https://dlstreams.top/watch.php?id=81")

    assert wrapper.channel.id == 81
    assert wrapper.channel.name == "ESPN Brasil"
    assert wrapper.player.primary.url == "https://dlstreams.top/stream/stream-81.php"
    assert [player.label for player in wrapper.player.alternates] == ["Player 1", "Player 2", "Player 3"]
    assert wrapper.related.label == "Quick Switch"
    assert len(wrapper.related.channels) == 2


def test_extract_embed_proxy_manifest_from_premiumtv_fixture(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.resolve.player.build_session",
        lambda: _FakeSession({}),
    )

    manifests = _extract_embed_proxy_manifest(_fixture_text("embed_premiumtv_81.html"))

    assert manifests == ["https://chevy.enviromentalspa2.sbs/proxy/zeko/premium81/mono.css"]


def test_extract_embed_proxy_manifest_from_url_uses_fixture_html(monkeypatch) -> None:
    iframe_url = "https://viewembed.ru/channel/ESPNBrazil[Brazil]"
    monkeypatch.setattr(
        "app.resolve.player.build_session",
        lambda: _FakeSession({iframe_url: _fixture_text("embed_viewembed_espnbrazil.html")}),
    )

    manifests = _extract_embed_proxy_manifest_from_url(iframe_url, "https://dlstreams.top/watch/stream-81.php")

    assert manifests == ["https://chevy.soyspace.cyou/proxy/x4/espnbrazil/mono.css"]


def test_bootstrap_inputs_prefer_specific_strategy_before_fallback() -> None:
    results = bootstrap_parse_results(
        _fixture_text("embed_viewembed_espnbrazil.html"),
        source_url="https://viewembed.ru/channel/ESPNBrazil[Brazil]",
    )

    assert results[0].strategy_name == "topembed-channel"
    assert results[0].reason == "parsed"
    assert results[0].inputs is not None


def test_bootstrap_parse_exposes_reason_when_markers_are_missing() -> None:
    results = bootstrap_parse_results(
        "<html><body>no bootstrap markers here</body></html>",
        source_url="https://viewembed.ru/channel/ESPNBrazil[Brazil]",
    )

    assert results[0].strategy_name == "topembed-channel"
    assert results[0].reason == "missing_markers"
    assert results[0].inputs is None


def test_bootstrap_context_reprioritizes_strategy_for_player_type() -> None:
    iframe_url = "https://viewembed.ru/embed/ESPNBrazil"

    assert matching_bootstrap_strategy_names(iframe_url) == ("known-host-fallback", "topembed-channel")
    assert matching_bootstrap_strategy_names(iframe_url, player_url="https://dlstreams.top/watch/stream-81.php") == (
        "topembed-channel",
        "known-host-fallback",
    )


def test_bootstrap_from_url_uses_player_context_for_known_host_path_drift(monkeypatch) -> None:
    iframe_url = "https://viewembed.ru/embed/ESPNBrazil"
    monkeypatch.setattr(
        "app.resolve.player.build_session",
        lambda: _FakeSession({iframe_url: _fixture_text("embed_viewembed_espnbrazil.html")}),
    )

    manifests = _extract_embed_proxy_manifest_from_url(
        iframe_url,
        "https://dlstreams.top/watch/stream-81.php",
        player_url="https://dlstreams.top/watch/stream-81.php",
        player_label="Player 3",
    )

    assert manifests == ["https://chevy.soyspace.cyou/proxy/x4/espnbrazil/mono.css"]


def test_hls_fixture_looks_like_playlist_and_extracts_targets() -> None:
    body = _fixture_text("hls_valid_playlist.m3u8")

    assert _looks_like_hls_playlist(body) is True
    key_url, media_url = _extract_hls_probe_targets(body, "https://chevy.enviromentalspa2.sbs/proxy/zeko/premium81/mono.css")
    assert key_url == "https://chevy.enviromentalspa2.sbs/key/premium81/5920860"
    assert media_url == "https://chevy.enviromentalspa2.sbs/redirect/media/cnxfeeconfessioncxv/static/eplayer_cc087fa7b0c2253b.js?subdomain=f006"


def test_provider_bootstrap_rules_reject_unrelated_iframe_hosts() -> None:
    assert should_attempt_bootstrap_fetch("https://enviromentalspa2.sbs/premiumtv/daddyhd.php?id=81") is True
    assert should_attempt_bootstrap_fetch("https://domaintransver.cfd/premiumtv/daddyhd.php?id=81") is True
    assert should_attempt_bootstrap_fetch("https://viewembed.ru/channel/ESPNBrazil[Brazil]") is True
    assert should_attempt_bootstrap_fetch("https://ads.example.test/frame.html") is False
