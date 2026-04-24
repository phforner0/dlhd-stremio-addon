from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import (
    _dynamic_proxy_host_cache_key,
    _host_allowed,
    _remember_dynamic_manifest_host,
    app,
    dynamic_proxy_host_cache,
    playlist_cache,
)
from app.upstream_health import UpstreamCircuitOpen


class FakeResponse:
    def __init__(self, url: str, status_code: int = 200, headers: dict[str, str] | None = None, text: str = "", content: bytes = b""):
        self.url = url
        self.status_code = status_code
        self.headers = headers or {}
        self._text = text
        self._content = content

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")

    @property
    def text(self) -> str:
        return self._text

    def iter_content(self, chunk_size: int = 65536):
        if self._content:
            yield self._content

    def close(self) -> None:
        return None


class FakeSession:
    def __init__(self, responses: dict[str, FakeResponse]):
        self.responses = responses
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs.get("headers", {})))
        return self.responses[url]


def test_proxy_rejects_disallowed_host() -> None:
    client = TestClient(app)

    response = client.get(
        "/proxy/stream.m3u8",
        params={"url": "https://example.com/evil.m3u8", "referer": "https://embedkclx.sbs/premiumtv/daddyhd.php?id=81"},
    )

    assert response.status_code == 403


def test_proxy_allowlist_includes_new_embed_hosts() -> None:
    assert _host_allowed("enviromentalspa2.sbs") is True
    assert _host_allowed("chevy.enviromentalspa2.sbs") is True
    assert _host_allowed("viewembed.ru") is True
    assert _host_allowed("chevy.soyspace.cyou") is True
    assert _host_allowed("chevy.vovlacosa.sbs") is True
    assert _host_allowed("img.aiphotofree.site") is True
    assert _host_allowed("ddyplayer.cfd") is True
    assert _host_allowed("edge.cdnlivetv.ru") is True
    assert _host_allowed("edge.cdn-aws.ru") is True


def test_proxy_allows_trusted_dynamic_manifest_host(monkeypatch) -> None:
    client = TestClient(app)
    dynamic_host = "chevy.rotatingtest.sbs"
    dynamic_proxy_host_cache.delete(_dynamic_proxy_host_cache_key(dynamic_host))
    session = FakeSession(
        {
            "https://chevy.rotatingtest.sbs/proxy/wind/premium88/mono.css": FakeResponse(
                "https://chevy.rotatingtest.sbs/proxy/wind/premium88/mono.css",
                headers={"Content-Type": "application/vnd.apple.mpegurl"},
                text="#EXTM3U\n#EXTINF:4,\nhttps://img.aiphotofree.site/static/segment.js\n",
            )
        }
    )
    monkeypatch.setattr("app.main.get_pooled_session", lambda: session)
    monkeypatch.setattr("app.main._public_host", lambda host: True)

    _remember_dynamic_manifest_host(
        "https://chevy.rotatingtest.sbs/proxy/wind/premium88/mono.css",
        "https://dlstreams.com/stream/stream-88.php",
    )
    response = client.get(
        "/proxy/stream.m3u8",
        params={
            "url": "https://chevy.rotatingtest.sbs/proxy/wind/premium88/mono.css",
            "referer": "https://dlstreams.com/stream/stream-88.php",
        },
    )

    assert response.status_code == 200
    dynamic_proxy_host_cache.delete(_dynamic_proxy_host_cache_key(dynamic_host))


def test_proxy_rejects_untrusted_dynamic_manifest_source() -> None:
    dynamic_host = "chevy.untrustedtest.sbs"
    dynamic_proxy_host_cache.delete(_dynamic_proxy_host_cache_key(dynamic_host))

    _remember_dynamic_manifest_host(
        "https://chevy.untrustedtest.sbs/proxy/wind/premium88/mono.css",
        "https://example.com/player.html",
    )

    assert _host_allowed(dynamic_host) is False


def test_proxy_rejects_private_ip(monkeypatch) -> None:
    client = TestClient(app)
    monkeypatch.setattr("app.main._host_allowed", lambda host: True)
    monkeypatch.setattr("app.main._public_host", lambda host: False)

    response = client.get(
        "/proxy/stream.m3u8",
        params={"url": "https://10.0.0.5/evil.m3u8", "referer": "https://embedkclx.sbs/premiumtv/daddyhd.php?id=81"},
    )

    assert response.status_code == 403


def test_proxy_rechecks_redirect_target(monkeypatch) -> None:
    client = TestClient(app)
    session = FakeSession(
        {
            "https://embedkclx.sbs/proxy/ok/mono.css": FakeResponse(
                "https://embedkclx.sbs/proxy/ok/mono.css",
                status_code=302,
                headers={"Location": "https://evil.example/playlist.m3u8"},
            )
        }
    )
    monkeypatch.setattr("app.main.get_pooled_session", lambda: session)
    monkeypatch.setattr("app.main._public_host", lambda host: True)

    response = client.get(
        "/proxy/stream.m3u8",
        params={"url": "https://embedkclx.sbs/proxy/ok/mono.css", "referer": "https://embedkclx.sbs/premiumtv/daddyhd.php?id=81"},
    )

    assert response.status_code == 403


def test_proxy_allows_redirect_media_chain_to_public_media_host(monkeypatch) -> None:
    client = TestClient(app)
    session = FakeSession(
        {
            "https://embedkclx.sbs/redirect/media/test/path.js": FakeResponse(
                "https://embedkclx.sbs/redirect/media/test/path.js",
                status_code=302,
                headers={"Location": "https://f003.dsfkjngkjndf.sbs/media/test/path.js"},
            ),
            "https://f003.dsfkjngkjndf.sbs/media/test/path.js": FakeResponse(
                "https://f003.dsfkjngkjndf.sbs/media/test/path.js",
                headers={"Content-Type": "application/javascript"},
                content=b"\xa5\x00\x01",
            ),
        }
    )
    monkeypatch.setattr("app.main.get_pooled_session", lambda: session)
    monkeypatch.setattr("app.main._public_host", lambda host: True)

    response = client.get(
        "/proxy/path.js",
        params={"url": "https://embedkclx.sbs/redirect/media/test/path.js", "referer": "https://embedkclx.sbs/premiumtv/daddyhd.php?id=81"},
    )

    assert response.status_code == 200


def test_proxy_rejects_media_chain_to_unlisted_public_host(monkeypatch) -> None:
    client = TestClient(app)
    session = FakeSession(
        {
            "https://embedkclx.sbs/redirect/media/test/path.js": FakeResponse(
                "https://embedkclx.sbs/redirect/media/test/path.js",
                status_code=302,
                headers={"Location": "https://public.example.test/media/test/path.js"},
            )
        }
    )
    monkeypatch.setattr("app.main.get_pooled_session", lambda: session)
    monkeypatch.setattr("app.main._public_host", lambda host: True)

    response = client.get(
        "/proxy/path.js",
        params={"url": "https://embedkclx.sbs/redirect/media/test/path.js", "referer": "https://embedkclx.sbs/premiumtv/daddyhd.php?id=81"},
    )

    assert response.status_code == 403


def test_proxy_playlist_cache_isolated_by_host(monkeypatch) -> None:
    client = TestClient(app)
    playlist_cache.prune()
    playlist_cache.delete("playlist:https://a.example|https://embedkclx.sbs/proxy/test/mono.css|https://embedkclx.sbs/premiumtv/daddyhd.php?id=81")
    playlist_cache.delete("playlist:https://b.example|https://embedkclx.sbs/proxy/test/mono.css|https://embedkclx.sbs/premiumtv/daddyhd.php?id=81")

    session = FakeSession(
        {
            "https://embedkclx.sbs/proxy/test/mono.css": FakeResponse(
                "https://embedkclx.sbs/proxy/test/mono.css",
                headers={"Content-Type": "application/vnd.apple.mpegurl"},
                text="#EXTM3U\nsegment.ts\n",
            )
        }
    )
    monkeypatch.setattr("app.main.get_pooled_session", lambda: session)
    monkeypatch.setattr("app.main._public_host", lambda host: True)

    response_a = client.get(
        "/proxy/stream.m3u8",
        headers={"host": "a.example"},
        params={"url": "https://embedkclx.sbs/proxy/test/mono.css", "referer": "https://embedkclx.sbs/premiumtv/daddyhd.php?id=81"},
    )
    response_b = client.get(
        "/proxy/stream.m3u8",
        headers={"host": "b.example"},
        params={"url": "https://embedkclx.sbs/proxy/test/mono.css", "referer": "https://embedkclx.sbs/premiumtv/daddyhd.php?id=81"},
    )

    assert "https://a.example/proxy/segment.ts" not in response_b.text
    assert "https://b.example/proxy/segment.ts" not in response_a.text
    assert response_a.status_code == 200
    assert response_b.status_code == 200


def test_proxy_rejects_oversized_playlist(monkeypatch) -> None:
    client = TestClient(app)
    session = FakeSession(
        {
            "https://embedkclx.sbs/proxy/test/mono.css": FakeResponse(
                "https://embedkclx.sbs/proxy/test/mono.css",
                headers={"Content-Type": "application/vnd.apple.mpegurl"},
                text="#EXTM3U\n" + "a" * 32,
            )
        }
    )
    monkeypatch.setattr("app.main.get_pooled_session", lambda: session)
    monkeypatch.setattr("app.main._public_host", lambda host: True)
    monkeypatch.setattr("app.settings.HLS_PLAYLIST_MAX_BYTES", 8)

    response = client.get(
        "/proxy/stream.m3u8",
        params={"url": "https://embedkclx.sbs/proxy/test/mono.css", "referer": "https://embedkclx.sbs/premiumtv/daddyhd.php?id=81"},
    )

    assert response.status_code == 502


def test_proxy_rejects_obfuscated_worker_playlist(monkeypatch) -> None:
    client = TestClient(app)
    session = FakeSession(
        {
            "https://chevy.soyspace.cyou/proxy/x4/espnbrazil/mono.css": FakeResponse(
                "https://chevy.soyspace.cyou/proxy/x4/espnbrazil/mono.css",
                headers={"Content-Type": "application/vnd.apple.mpegurl"},
                text=(
                    "#EXTM3U\n"
                    "# uploader-meta: version=3.1.95; mode=s3; delivery=workers\n"
                    "#EXTINF:2,\n"
                    "https://img.aiphotofree.site/static/fake.js\n"
                ),
            )
        }
    )
    monkeypatch.setattr("app.main.get_pooled_session", lambda: session)
    monkeypatch.setattr("app.main._public_host", lambda host: True)

    response = client.get(
        "/proxy/stream.m3u8",
        params={
            "url": "https://chevy.soyspace.cyou/proxy/x4/espnbrazil/mono.css",
            "referer": "https://dlstreams.com/watch/stream-81.php",
        },
    )

    assert response.status_code == 502


def test_proxy_passes_range_header(monkeypatch) -> None:
    client = TestClient(app)
    session = FakeSession(
        {
            "https://vid.aivideox.site/media.ts": FakeResponse(
                "https://vid.aivideox.site/media.ts",
                headers={"Content-Type": "application/octet-stream", "Content-Range": "bytes 0-3/4"},
                content=b"test",
            )
        }
    )
    monkeypatch.setattr("app.main.get_pooled_session", lambda: session)
    monkeypatch.setattr("app.main._public_host", lambda host: True)

    response = client.get(
        "/proxy/media.ts",
        headers={"Range": "bytes=0-3"},
        params={"url": "https://vid.aivideox.site/media.ts", "referer": "https://embedkclx.sbs/premiumtv/daddyhd.php?id=81"},
    )

    assert response.status_code == 200
    assert session.calls[0][1]["Range"] == "bytes=0-3"


def test_proxy_normalizes_css_media_segments_to_octet_stream(monkeypatch) -> None:
    client = TestClient(app)
    session = FakeSession(
        {
            "https://img.aiphotofree.site/static/segment.css": FakeResponse(
                "https://img.aiphotofree.site/static/segment.css",
                headers={"Content-Type": "text/css"},
                content=b"\xa5\x00\x01",
            )
        }
    )
    monkeypatch.setattr("app.main.get_pooled_session", lambda: session)
    monkeypatch.setattr("app.main._public_host", lambda host: True)

    response = client.get(
        "/proxy/segment.css",
        params={
            "url": "https://img.aiphotofree.site/static/segment.css",
            "referer": "https://dlstreams.com/stream/stream-81.php",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"


def test_proxy_returns_503_when_upstream_circuit_is_open(monkeypatch) -> None:
    client = TestClient(app)
    monkeypatch.setattr("app.main.guarded_get", lambda *args, **kwargs: (_ for _ in ()).throw(UpstreamCircuitOpen("vid.aivideox.site", "proxy_upstream")))
    monkeypatch.setattr("app.main._public_host", lambda host: True)

    response = client.get(
        "/proxy/media.ts",
        params={"url": "https://vid.aivideox.site/media.ts", "referer": "https://embedkclx.sbs/premiumtv/daddyhd.php?id=81"},
    )

    assert response.status_code == 503
