from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from app.logging_utils import config_fingerprint, proxy_url_fields
from app.main import (
    _encode_user_config,
    _valid_hls_stream,
    app,
    manifest_validation_cache,
    manifest_validation_reason_cache,
)


class _FakeSession:
    def close(self) -> None:
        return None


class _FakeResponse:
    def __init__(self, url: str, content_type: str, text: str) -> None:
        self.url = url
        self.headers = {"Content-Type": content_type}
        self.text = text

    def raise_for_status(self) -> None:
        return None

    def close(self) -> None:
        return None


def test_config_fingerprint_is_order_independent() -> None:
    left = config_fingerprint({"catalogMode": "focused", "preferredCountryCode": "br"})
    right = config_fingerprint({"preferredCountryCode": "br", "catalogMode": "focused"})

    assert left == right


def test_proxy_url_fields_redact_query_and_normalize_tokens() -> None:
    fields = proxy_url_fields("https://foo.example/proxy/123/cfg-abcdef123456/abcd1234ef567890.js?token=secret")

    assert fields["upstream_host"] == "foo.example"
    assert fields["upstream_path"] == "/proxy/:id/:config/:hex.js"
    assert fields["upstream_url_hash"] is not None


def test_manifest_logs_hash_without_raw_config_token(caplog) -> None:
    client = TestClient(app)
    config = {"catalogMode": "focused", "preferredCountryCode": "br"}
    token = _encode_user_config(config)

    caplog.set_level(logging.INFO, logger="dlhd.addon")

    response = client.get(f"/{token}/manifest.json")
    addon_messages = [record.getMessage() for record in caplog.records if record.name == "dlhd.addon"]

    assert response.status_code == 200
    assert "manifest_served" in caplog.text
    assert config_fingerprint(config) in caplog.text
    assert addon_messages
    assert all(token not in message for message in addon_messages)


def test_proxy_rejection_logs_reason_without_full_url(caplog) -> None:
    client = TestClient(app)

    caplog.set_level(logging.WARNING, logger="dlhd.addon")

    response = client.get(
        "/proxy/stream.m3u8",
        params={
            "url": "https://example.com/evil.m3u8?token=secret",
            "referer": "https://embedkclx.sbs/premiumtv/daddyhd.php?id=81",
        },
    )

    assert response.status_code == 403
    assert "proxy_url_rejected" in caplog.text
    assert "host_not_allowlisted" in caplog.text
    assert "https://example.com/evil.m3u8?token=secret" not in caplog.text


def test_hls_validation_logs_failure_reason(monkeypatch, caplog) -> None:
    manifest_url = "https://embedkclx.sbs/proxy/test/mono.css"
    referer = "https://embedkclx.sbs/premiumtv/daddyhd.php?id=81"
    cache_key = f"hls-valid:{manifest_url}|{referer}"
    manifest_validation_cache.delete(cache_key)
    manifest_validation_reason_cache.delete(cache_key)

    monkeypatch.setattr("app.main.build_session", lambda: _FakeSession())
    monkeypatch.setattr(
        "app.main._follow_proxy_redirects",
        lambda session, url, headers, log_fields=None: _FakeResponse(url, "application/vnd.apple.mpegurl", "not a playlist"),
    )

    caplog.set_level(logging.DEBUG, logger="dlhd.addon")

    assert _valid_hls_stream(manifest_url, referer) is False
    assert "hls_validation_fail" in caplog.text
    assert "not_extm3u" in caplog.text
