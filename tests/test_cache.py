from __future__ import annotations

import time
import threading

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

    cache.prune()

    assert cache.get("beta") is None
    assert "beta" not in cache._entries
    assert "beta" not in cache._locks
