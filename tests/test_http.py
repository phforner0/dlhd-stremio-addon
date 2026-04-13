from __future__ import annotations

from app.http import build_session, close_pooled_sessions, get_pooled_session


def test_build_session_uses_safe_retries_by_default() -> None:
    session = build_session()
    adapter = session.get_adapter("https://")

    assert adapter.max_retries.total == 2
    assert 429 in adapter.max_retries.status_forcelist
    session.close()


def test_pooled_session_disables_safe_retries() -> None:
    close_pooled_sessions()

    session = get_pooled_session()
    adapter = session.get_adapter("https://")

    assert adapter.max_retries.total == 0
    close_pooled_sessions()
