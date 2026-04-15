from __future__ import annotations

import logging
import threading
import time

from app.cache import TTLCache


def test_ttl_cache_delete_removes_value_and_lock() -> None:
    cache: TTLCache[str] = TTLCache()

    assert cache.remember("alpha", 60, lambda: "value") == "value"
    assert cache.get("alpha") == "value"
    cache._locks["alpha"] = threading.Lock()

    cache.delete("alpha")

    assert cache.get("alpha") is None
    assert "alpha" not in cache._locks


def test_ttl_cache_prune_removes_expired_entry_and_lock() -> None:
    cache: TTLCache[str] = TTLCache()

    cache.remember("beta", 1, lambda: "value")
    cache._entries["beta"].expires_at = time.time() - 1
    cache._entries["beta"].stale_until = time.time() - 1

    cache.prune()

    assert cache.get("beta") is None
    assert "beta" not in cache._entries
    assert "beta" not in cache._locks


def test_ttl_cache_evicts_oldest_when_max_entries_exceeded() -> None:
    cache: TTLCache[str] = TTLCache(max_entries=2)

    cache.set("alpha", "a", 60)
    cache.set("beta", "b", 60)
    cache.set("gamma", "c", 60)

    assert cache.get("alpha") is None
    assert cache.get("beta") == "b"
    assert cache.get("gamma") == "c"


def test_ttl_cache_remember_stale_returns_stale_and_refreshes_in_background() -> None:
    cache: TTLCache[str] = TTLCache()
    cache.set("alpha", "old", 0, stale_ttl_seconds=60)
    refresh_done = threading.Event()

    def factory() -> str:
        refresh_done.set()
        return "new"

    value = cache.remember_stale("alpha", 60, 60, factory)

    assert value == "old"
    assert refresh_done.wait(timeout=2) is True
    assert cache.get("alpha") == "new"


def test_ttl_cache_logs_set_and_hit(caplog) -> None:
    cache: TTLCache[str] = TTLCache(name="demo")

    caplog.set_level(logging.DEBUG, logger="dlhd.cache")

    cache.set("alpha", "value", 60)
    assert cache.get("alpha") == "value"

    assert "cache_set" in caplog.text
    assert "cache_hit" in caplog.text
    assert "demo" in caplog.text
