from __future__ import annotations

from collections import OrderedDict
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

from app.logging_utils import log_event

T = TypeVar("T")
LOGGER = logging.getLogger("dlhd.cache")


@dataclass(slots=True)
class _Entry(Generic[T]):
    value: T
    expires_at: float
    stale_until: float


class TTLCache(Generic[T]):
    def __init__(self, *, max_entries: int | None = None, name: str = "cache") -> None:
        self._entries: OrderedDict[str, _Entry[T]] = OrderedDict()
        self._locks: dict[str, threading.Lock] = {}
        self._refreshing: set[str] = set()
        self._guard = threading.Lock()
        self._max_entries = max_entries
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def entry_count(self) -> int:
        with self._guard:
            return len(self._entries)

    def _key_class(self, key: str) -> str:
        return key.split(":", 1)[0] if ":" in key else "default"

    def _base_fields(self, key: str, **extra: object) -> dict[str, object]:
        return {
            "cache_name": self._name,
            "cache_key_class": self._key_class(key),
            "max_entries": self._max_entries,
            **extra,
        }

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
            log_event(
                LOGGER,
                logging.DEBUG,
                "cache_evicted",
                **self._base_fields(evict_key, entry_count=len(self._entries)),
            )

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
                log_event(LOGGER, logging.DEBUG, "cache_miss", **self._base_fields(key, cache_status="missing"))
                return None
            if not allow_stale and entry.expires_at <= now:
                log_event(LOGGER, logging.DEBUG, "cache_miss", **self._base_fields(key, cache_status="expired"))
                return None
            self._touch_unlocked(key)
            event = "cache_stale_hit" if entry.expires_at <= now else "cache_hit"
            log_event(LOGGER, logging.DEBUG, event, **self._base_fields(key, cache_status="stale" if event == "cache_stale_hit" else "fresh"))
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
            entry_count = len(self._entries)
        log_event(
            LOGGER,
            logging.DEBUG,
            "cache_set",
            **self._base_fields(
                key,
                ttl_seconds=ttl_seconds,
                stale_ttl_seconds=stale_ttl_seconds,
                entry_count=entry_count,
            ),
        )
        return value

    def delete(self, key: str) -> None:
        with self._guard:
            self._entries.pop(key, None)
            self._locks.pop(key, None)
            self._refreshing.discard(key)

    def prune(self) -> None:
        with self._guard:
            before = len(self._entries)
            self._prune_expired_unlocked()
            removed = before - len(self._entries)
            entry_count = len(self._entries)
        if removed:
            log_event(
                LOGGER,
                logging.DEBUG,
                "cache_pruned",
                cache_name=self._name,
                entry_count=entry_count,
                removed_count=removed,
                max_entries=self._max_entries,
            )

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
        log_event(
            LOGGER,
            logging.DEBUG,
            "cache_refresh_start",
            **self._base_fields(key, ttl_seconds=ttl_seconds, stale_ttl_seconds=stale_ttl_seconds),
        )
        try:
            self._compute_and_store(key, ttl_seconds, stale_ttl_seconds, factory)
            log_event(
                LOGGER,
                logging.DEBUG,
                "cache_refresh_end",
                **self._base_fields(key, ttl_seconds=ttl_seconds, stale_ttl_seconds=stale_ttl_seconds),
            )
        except Exception as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                "cache_refresh_fail",
                **self._base_fields(
                    key,
                    ttl_seconds=ttl_seconds,
                    stale_ttl_seconds=stale_ttl_seconds,
                    reason=exc.__class__.__name__,
                ),
            )
            raise
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
                    log_event(
                        LOGGER,
                        logging.DEBUG,
                        "cache_stale_served",
                        **self._base_fields(key, ttl_seconds=ttl_seconds, stale_ttl_seconds=stale_ttl_seconds),
                    )
                    threading.Thread(
                        target=self._refresh_in_background,
                        args=(key, ttl_seconds, stale_ttl_seconds, factory),
                        daemon=True,
                    ).start()
                return entry.value

        return self._compute_and_store(key, ttl_seconds, stale_ttl_seconds, factory)
