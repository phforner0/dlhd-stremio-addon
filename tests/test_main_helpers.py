from __future__ import annotations

from app.main import _looks_like_hls_playlist


def test_hls_playlist_detection_accepts_extm3u() -> None:
    body = "#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:5.0,\nsegment.ts\n"

    assert _looks_like_hls_playlist(body) is True


def test_hls_playlist_detection_rejects_non_playlist_text() -> None:
    body = "console.log('not a playlist');"

    assert _looks_like_hls_playlist(body) is False
