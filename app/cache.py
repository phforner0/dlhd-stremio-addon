from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


@dataclass(slots=True)
class _Entry(Generic[T]):
    value: T
    expires_at: float


class TTLCache(Generic[T]):
    def __init__(self) -> None:
        self._entries: dict[str, _Entry[T]] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

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
            if entry.expires_at <= current_time
        ]
        for key in expired_keys:
            self._entries.pop(key, None)
            self._locks.pop(key, None)

        live_keys = set(self._entries) | keep_keys
        stale_locks = [key for key in self._locks if key not in live_keys]
        for key in stale_locks:
            self._locks.pop(key, None)

    def get(self, key: str) -> T | None:
        now = time.time()
        with self._guard:
            self._prune_expired_unlocked(now)
            entry = self._entries.get(key)
            if not entry:
                return None
            return entry.value

    def set(self, key: str, value: T, ttl_seconds: int) -> T:
        with self._guard:
            self._prune_expired_unlocked(preserve_keys={key})
            self._entries[key] = _Entry(value=value, expires_at=time.time() + ttl_seconds)
        return value

    def delete(self, key: str) -> None:
        with self._guard:
            self._entries.pop(key, None)
            self._locks.pop(key, None)

    def prune(self) -> None:
        with self._guard:
            self._prune_expired_unlocked()

    def remember(self, key: str, ttl_seconds: int, factory: Callable[[], T]) -> T:
        cached = self.get(key)
        if cached is not None:
            return cached

        with self._guard:
            key_lock = self._locks.setdefault(key, threading.Lock())

        with key_lock:
            cached = self.get(key)
            if cached is not None:
                return cached
            value = factory()
            self.set(key, value, ttl_seconds)
            return value
