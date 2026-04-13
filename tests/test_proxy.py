from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app, playlist_cache


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
