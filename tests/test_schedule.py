from __future__ import annotations

from datetime import datetime, timezone

from app.scrape import schedule as schedule_module


def test_parse_schedule_datetime_utc_from_day_label_and_time() -> None:
    scheduled = schedule_module._parse_schedule_datetime_utc(
        "Sunday 12th April 2026 - Schedule Time UK GMT",
        "14:00",
    )

    assert scheduled == datetime(2026, 4, 12, 14, 0, tzinfo=timezone.utc)


def test_should_include_event_filters_old_entries(monkeypatch) -> None:
    monkeypatch.setattr(
        schedule_module,
        "_schedule_now_utc",
        lambda: datetime(2026, 4, 12, 20, 0, tzinfo=timezone.utc),
    )

    assert schedule_module._should_include_event(datetime(2026, 4, 12, 17, 0, tzinfo=timezone.utc)) is True
    assert schedule_module._should_include_event(datetime(2026, 4, 12, 13, 59, tzinfo=timezone.utc)) is False


def test_display_schedule_values_apply_offset(monkeypatch) -> None:
    monkeypatch.setattr(schedule_module.settings, "SCHEDULE_DISPLAY_GMT_OFFSET_MINUTES", -180)
    day_label, time_text = schedule_module._display_schedule_values(
        "Sunday 12th April 2026 - Schedule Time UK GMT",
        "14:00",
        datetime(2026, 4, 12, 14, 0, tzinfo=timezone.utc),
        -180,
    )

    assert day_label == "Sunday 12th April 2026 - Schedule Time GMT -03:00"
    assert time_text == "11:00"
