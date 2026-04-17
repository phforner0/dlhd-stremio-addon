from __future__ import annotations

from app import settings
from app.resolve.player import _extract_embed_proxy_manifest_from_url, _http_resolve_player_page, _new_wait_state, _should_ignore_https_errors, _wait_for_resolution_window


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class _FakeSession:
    def __init__(self, text: str) -> None:
        self.headers: dict[str, str] = {}
        self._text = text

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def get(self, url: str, timeout: int):
        return _FakeResponse(self._text)


class _FakeHttpResponse:
    def __init__(self, url: str, text: str) -> None:
        self.url = url
        self.text = text

    def raise_for_status(self) -> None:
        return None


class _FakePage:
    def __init__(self, now_ref: list[float], frame_count: int) -> None:
        self._now_ref = now_ref
        self._frame_count = frame_count

    @property
    def frames(self):
        return [object()] * self._frame_count

    def wait_for_timeout(self, remaining_ms: int) -> None:
        self._now_ref[0] += remaining_ms / 1000


def test_ignore_https_errors_disabled_by_default(monkeypatch) -> None:
    monkeypatch.setattr(settings, "PLAYWRIGHT_IGNORE_HTTPS_ERRORS", False)
    monkeypatch.setattr(settings, "PLAYWRIGHT_IGNORE_HTTPS_ERROR_HOSTS", tuple())

    assert _should_ignore_https_errors("https://example.com/player") is False


def test_ignore_https_errors_enabled_by_global_flag(monkeypatch) -> None:
    monkeypatch.setattr(settings, "PLAYWRIGHT_IGNORE_HTTPS_ERRORS", True)
    monkeypatch.setattr(settings, "PLAYWRIGHT_IGNORE_HTTPS_ERROR_HOSTS", tuple())

    assert _should_ignore_https_errors("https://example.com/player") is True


def test_ignore_https_errors_enabled_for_allowlisted_host(monkeypatch) -> None:
    monkeypatch.setattr(settings, "PLAYWRIGHT_IGNORE_HTTPS_ERRORS", False)
    monkeypatch.setattr(settings, "PLAYWRIGHT_IGNORE_HTTPS_ERROR_HOSTS", (".embedkclx.sbs",))

    assert _should_ignore_https_errors("https://embedkclx.sbs/premiumtv/daddyhd.php?id=81") is True
    assert _should_ignore_https_errors("https://foo.embedkclx.sbs/path") is True
    assert _should_ignore_https_errors("https://dlstreams.top/stream/stream-81.php") is False


def test_extract_embed_proxy_manifest_from_url_accepts_new_iframe_hosts(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.resolve.player.build_session",
        lambda: _FakeSession("const CHANNEL_KEY = 'premium81'; let M3U8_SERVERS = ['example.test']; server_lookup"),
    )
    monkeypatch.setattr(
        "app.resolve.player._extract_embed_proxy_manifest_with_source",
        lambda html, timeout=10, source_url=None, player_url=None, player_label=None: ["https://example.test/proxy/premium81/mono.css"],
    )

    manifests = _extract_embed_proxy_manifest_from_url(
        "https://enviromentalspa2.sbs/premiumtv/daddyhd.php?id=81",
        "https://dlstreams.top/stream/stream-81.php",
    )

    assert manifests == ["https://example.test/proxy/premium81/mono.css"]


def test_extract_embed_proxy_manifest_from_url_accepts_non_premiumtv_paths(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.resolve.player.build_session",
        lambda: _FakeSession("const CHANNEL_KEY = 'espnbrazil'; let M3U8_SERVERS = ['example.test']; server_lookup"),
    )
    monkeypatch.setattr(
        "app.resolve.player._extract_embed_proxy_manifest_with_source",
        lambda html, timeout=10, source_url=None, player_url=None, player_label=None: ["https://example.test/proxy/espnbrazil/mono.css"],
    )

    manifests = _extract_embed_proxy_manifest_from_url(
        "https://viewembed.ru/channel/ESPNBrazil[Brazil]",
        "https://dlstreams.top/watch/stream-81.php",
    )

    assert manifests == ["https://example.test/proxy/espnbrazil/mono.css"]


def test_wait_for_resolution_window_does_not_exit_early_for_frame_only_signal(monkeypatch) -> None:
    now = [0.0]
    monkeypatch.setattr("app.resolve.player.time.monotonic", lambda: now[0])
    state = _new_wait_state()
    page = _FakePage(now, frame_count=2)

    _wait_for_resolution_window(page, state, 10000)

    assert now[0] >= 5.0


def test_http_resolve_player_page_extracts_static_iframe_bootstrap(monkeypatch) -> None:
    html = '<html><body><iframe src="https://domaintransver.cfd/premiumtv/daddyhd.php?id=81"></iframe></body></html>'

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    monkeypatch.setattr("app.resolve.player.build_session", lambda: FakeSession())
    monkeypatch.setattr(
        "app.resolve.player.guarded_get",
        lambda session, url, operation, timeout, headers: _FakeHttpResponse(url, html),
    )
    monkeypatch.setattr(
        "app.resolve.player._extract_embed_proxy_manifest_from_url",
        lambda url, referer, timeout=10, player_url=None, player_label=None: ["https://example.test/proxy/premium81/mono.css"],
    )

    hits, iframe_urls, final_url = _http_resolve_player_page("Player 1", "https://dlstreams.com/stream/stream-81.php")

    assert iframe_urls == ["https://domaintransver.cfd/premiumtv/daddyhd.php?id=81"]
    assert hits == [("https://example.test/proxy/premium81/mono.css", "https://dlstreams.com/stream/stream-81.php")]
    assert final_url == "https://dlstreams.com/stream/stream-81.php"
