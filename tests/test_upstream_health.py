from __future__ import annotations

import pytest

from app.upstream_health import UpstreamCircuitOpen, UpstreamHealth


def test_upstream_health_opens_circuit_after_threshold(monkeypatch) -> None:
    now = [100.0]
    monkeypatch.setattr("app.upstream_health.time.monotonic", lambda: now[0])
    health = UpstreamHealth(failure_threshold=2, failure_window_seconds=60, cooldown_seconds=30, max_hosts=16)

    health.record_failure("example.test", operation="proxy", reason="Timeout")
    health.before_request("example.test", operation="proxy")

    health.record_failure("example.test", operation="proxy", reason="Timeout")

    with pytest.raises(UpstreamCircuitOpen):
        health.before_request("example.test", operation="proxy")

    assert health.degraded_host_count() == 1


def test_upstream_health_success_clears_circuit(monkeypatch) -> None:
    now = [100.0]
    monkeypatch.setattr("app.upstream_health.time.monotonic", lambda: now[0])
    health = UpstreamHealth(failure_threshold=1, failure_window_seconds=60, cooldown_seconds=30, max_hosts=16)

    health.record_failure("example.test", operation="watch_fetch", reason="ConnectionError")
    with pytest.raises(UpstreamCircuitOpen):
        health.before_request("example.test", operation="watch_fetch")

    health.record_success("example.test", operation="watch_fetch")

    health.before_request("example.test", operation="watch_fetch")
    assert health.degraded_host_count() == 0
