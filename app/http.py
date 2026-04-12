from __future__ import annotations

import threading

import requests
from requests.adapters import HTTPAdapter

from app import settings

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
}

_thread_local = threading.local()
_pooled_sessions: dict[int, requests.Session] = {}
_pooled_guard = threading.Lock()


def _configure_session(session: requests.Session) -> requests.Session:
    adapter = HTTPAdapter(
        pool_connections=settings.HTTP_POOL_CONNECTIONS,
        pool_maxsize=settings.HTTP_POOL_MAXSIZE,
        max_retries=0,
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(DEFAULT_HEADERS)
    return session


def build_session() -> requests.Session:
    return _configure_session(requests.Session())


def get_pooled_session() -> requests.Session:
    session = getattr(_thread_local, "pooled_session", None)
    if session is not None:
        return session

    session = build_session()
    _thread_local.pooled_session = session
    with _pooled_guard:
        _pooled_sessions[threading.get_ident()] = session
    return session


def close_pooled_sessions() -> None:
    with _pooled_guard:
        sessions = list(_pooled_sessions.values())
        _pooled_sessions.clear()

    for session in sessions:
        try:
            session.close()
        except Exception:
            pass
