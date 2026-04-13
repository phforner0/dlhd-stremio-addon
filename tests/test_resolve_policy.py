from __future__ import annotations

from app import settings
from app.resolve.player import _should_ignore_https_errors


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
