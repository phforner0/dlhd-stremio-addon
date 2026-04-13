from __future__ import annotations

from collections import OrderedDict
import threading
import time
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


@dataclass(slots=True)
class _Entry(Generic[T]):
    value: T
    expires_at: float
    stale_until: float


class TTLCache(Generic[T]):
    def __init__(self, *, max_entries: int | None = None) -> None:
        self._entries: OrderedDict[str, _Entry[T]] = OrderedDict()
        self._locks: dict[str, threading.Lock] = {}
        self._refreshing: set[str] = set()
        self._guard = threading.Lock()
        self._max_entries = max_entries

    def _touch_unlocked(self, key: str) -> None:
        self._entries.move_to_end(key)

    def _evict_overflow_unlocked(self, preserve_keys: set[str] | None = None) -> None:
        if self._max_entries is None or self._max_entries <= 0:
            return

        protected = preserve_keys or set()
        while len(self._entries) > self._max_entries:
            evict_key = next((key for key in self._entries if key not in protected), None)
            if evict_key is None:
                return
            self._entries.pop(evict_key, None)
            self._locks.pop(evict_key, None)
            self._refreshing.discard(evict_key)

    def _prune_expired_unlocked(
        self,
        now: float | None = None,
        preserve_keys: set[str] | None = None,
    ) -> None:
        current_time = time.time() if now is None else now
        keep_keys = preserve_keys or set()
        expired_keys = [
            key
            for key, entry in self._entries.items()
            if entry.stale_until <= current_time
        ]
        for key in expired_keys:
            self._entries.pop(key, None)
            self._locks.pop(key, None)
            self._refreshing.discard(key)

        live_keys = set(self._entries) | keep_keys | self._refreshing
        stale_locks = [key for key in self._locks if key not in live_keys]
        for key in stale_locks:
            self._locks.pop(key, None)

    def get(self, key: str, *, allow_stale: bool = False) -> T | None:
        now = time.time()
        with self._guard:
            self._prune_expired_unlocked(now)
            entry = self._entries.get(key)
            if not entry:
                return None
            if not allow_stale and entry.expires_at <= now:
                return None
            self._touch_unlocked(key)
            return entry.value

    def set(self, key: str, value: T, ttl_seconds: int, *, stale_ttl_seconds: int = 0) -> T:
        now = time.time()
        with self._guard:
            self._prune_expired_unlocked(preserve_keys={key})
            expires_at = now + ttl_seconds
            stale_until = expires_at + max(stale_ttl_seconds, 0)
            self._entries[key] = _Entry(value=value, expires_at=expires_at, stale_until=stale_until)
            self._touch_unlocked(key)
            self._evict_overflow_unlocked({key})
        return value

    def delete(self, key: str) -> None:
        with self._guard:
            self._entries.pop(key, None)
            self._locks.pop(key, None)
            self._refreshing.discard(key)

    def prune(self) -> None:
        with self._guard:
            self._prune_expired_unlocked()

    def _compute_and_store(self, key: str, ttl_seconds: int, stale_ttl_seconds: int, factory: Callable[[], T]) -> T:
        with self._guard:
            key_lock = self._locks.setdefault(key, threading.Lock())

        with key_lock:
            cached = self.get(key)
            if cached is not None:
                return cached

            value = factory()
            self.set(key, value, ttl_seconds, stale_ttl_seconds=stale_ttl_seconds)
            return value

    def _refresh_in_background(
        self,
        key: str,
        ttl_seconds: int,
        stale_ttl_seconds: int,
        factory: Callable[[], T],
    ) -> None:
        try:
            self._compute_and_store(key, ttl_seconds, stale_ttl_seconds, factory)
        finally:
            with self._guard:
                self._refreshing.discard(key)
                self._prune_expired_unlocked()

    def remember(self, key: str, ttl_seconds: int, factory: Callable[[], T]) -> T:
        cached = self.get(key)
        if cached is not None:
            return cached

        return self._compute_and_store(key, ttl_seconds, 0, factory)

    def remember_stale(
        self,
        key: str,
        ttl_seconds: int,
        stale_ttl_seconds: int,
        factory: Callable[[], T],
    ) -> T:
        now = time.time()
        with self._guard:
            self._prune_expired_unlocked(now)
            entry = self._entries.get(key)
            if entry is not None:
                self._touch_unlocked(key)
                if entry.expires_at > now:
                    return entry.value

                if key not in self._refreshing:
                    self._refreshing.add(key)
                    threading.Thread(
                        target=self._refresh_in_background,
                        args=(key, ttl_seconds, stale_ttl_seconds, factory),
                        daemon=True,
                    ).start()
                return entry.value

        return self._compute_and_store(key, ttl_seconds, stale_ttl_seconds, factory)
