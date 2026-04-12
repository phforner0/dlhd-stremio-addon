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

    def get(self, key: str) -> T | None:
        now = time.time()
        with self._guard:
            entry = self._entries.get(key)
            if not entry:
                return None
            if entry.expires_at <= now:
                self._entries.pop(key, None)
                return None
            return entry.value

    def set(self, key: str, value: T, ttl_seconds: int) -> T:
        with self._guard:
            self._entries[key] = _Entry(value=value, expires_at=time.time() + ttl_seconds)
        return value

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
