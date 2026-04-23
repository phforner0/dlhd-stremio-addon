from __future__ import annotations

from dataclasses import dataclass
import logging
import threading
import time
from urllib.parse import urlparse

from app import settings
from app.logging_utils import log_event

LOGGER = logging.getLogger("dlhd.upstream")


@dataclass(slots=True)
class _HostState:
    window_started_at: float
    failure_count: int = 0
    cooldown_until: float = 0.0


class UpstreamCircuitOpen(Exception):
    def __init__(self, host: str, operation: str) -> None:
        super().__init__(f"upstream circuit open for {host}")
        self.host = host
        self.operation = operation


class UpstreamHealth:
    def __init__(
        self,
        *,
        failure_threshold: int,
        failure_window_seconds: int,
        cooldown_seconds: int,
        max_hosts: int,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._failure_window_seconds = failure_window_seconds
        self._cooldown_seconds = cooldown_seconds
        self._max_hosts = max_hosts
        self._guard = threading.Lock()
        self._states: dict[str, _HostState] = {}

    @property
    def enabled(self) -> bool:
        return (
            self._failure_threshold > 0
            and self._failure_window_seconds > 0
            and self._cooldown_seconds > 0
            and self._max_hosts > 0
        )

    def reset(self) -> None:
        with self._guard:
            self._states.clear()

    def degraded_host_count(self) -> int:
        now = time.monotonic()
        with self._guard:
            self._prune_unlocked(now)
            return sum(1 for state in self._states.values() if state.cooldown_until > now)

    def _prune_unlocked(self, now: float) -> None:
        stale_hosts = [
            host
            for host, state in self._states.items()
            if state.cooldown_until <= now and (now - state.window_started_at) > self._failure_window_seconds
        ]
        for host in stale_hosts:
            self._states.pop(host, None)

        while len(self._states) > self._max_hosts:
            oldest_host = min(self._states.items(), key=lambda item: item[1].window_started_at)[0]
            self._states.pop(oldest_host, None)

    def before_request(self, host: str | None, *, operation: str) -> None:
        if not self.enabled or not host:
            return

        now = time.monotonic()
        with self._guard:
            self._prune_unlocked(now)
            state = self._states.get(host)
            if state is None or state.cooldown_until <= now:
                return
            remaining_seconds = max(1, int(round(state.cooldown_until - now)))
            failure_count = state.failure_count

        log_event(
            LOGGER,
            logging.WARNING,
            "upstream_request_skipped",
            upstream_host=host,
            operation=operation,
            reason="circuit_open",
            cooldown_remaining_seconds=remaining_seconds,
            failure_count=failure_count,
        )
        raise UpstreamCircuitOpen(host, operation)

    def record_success(self, host: str | None, *, operation: str) -> None:
        if not self.enabled or not host:
            return

        now = time.monotonic()
        with self._guard:
            self._prune_unlocked(now)
            state = self._states.pop(host, None)

        if state is not None and (state.failure_count > 0 or state.cooldown_until > now):
            log_event(
                LOGGER,
                logging.INFO,
                "upstream_recovered",
                upstream_host=host,
                operation=operation,
                failure_count=state.failure_count,
            )

    def record_failure(self, host: str | None, *, operation: str, reason: str, status_code: int | None = None) -> None:
        if not self.enabled or not host:
            return

        now = time.monotonic()
        opened = False
        failure_count = 0
        with self._guard:
            self._prune_unlocked(now)
            state = self._states.get(host)
            if state is None or (now - state.window_started_at) > self._failure_window_seconds:
                state = _HostState(window_started_at=now)
                self._states[host] = state

            state.failure_count += 1
            failure_count = state.failure_count
            if state.failure_count >= self._failure_threshold and state.cooldown_until <= now:
                state.cooldown_until = now + self._cooldown_seconds
                opened = True
            self._prune_unlocked(now)

        if opened:
            log_event(
                LOGGER,
                logging.WARNING,
                "upstream_circuit_opened",
                upstream_host=host,
                operation=operation,
                reason=reason,
                status_code=status_code,
                failure_count=failure_count,
                cooldown_seconds=self._cooldown_seconds,
            )


def _failure_status(status_code: int | None) -> bool:
    return status_code == 429 or (isinstance(status_code, int) and status_code >= 500)


def guarded_get(session, url: str, *, operation: str, timeout: int | float | None = None, **kwargs):
    host = urlparse(url).hostname
    UPSTREAM_HEALTH.before_request(host, operation=operation)

    try:
        response = session.get(url, timeout=timeout, **kwargs)
    except Exception as exc:  # noqa: BLE001
        UPSTREAM_HEALTH.record_failure(host, operation=operation, reason=exc.__class__.__name__)
        raise

    status_code = getattr(response, "status_code", None)
    if _failure_status(status_code):
        UPSTREAM_HEALTH.record_failure(host, operation=operation, reason="http_error", status_code=status_code)
    else:
        UPSTREAM_HEALTH.record_success(host, operation=operation)
    return response


UPSTREAM_HEALTH = UpstreamHealth(
    failure_threshold=settings.UPSTREAM_FAILURE_THRESHOLD,
    failure_window_seconds=settings.UPSTREAM_FAILURE_WINDOW_SECONDS,
    cooldown_seconds=settings.UPSTREAM_COOLDOWN_SECONDS,
    max_hosts=settings.UPSTREAM_HEALTH_MAX_HOSTS,
)
