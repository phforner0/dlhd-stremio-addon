from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import logging
import re
from time import perf_counter
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
import requests

from app import settings
from app.http import build_session
from app.logging_utils import log_event, proxy_url_fields
from app.models import CatalogChannel, LiveEvent, ScheduleChannelLink
from app.normalize.country import classify_channel_country, classify_event_countries
from app.upstream_health import guarded_get

LOGGER = logging.getLogger("dlhd.scrape.schedule")

DAY_LABEL_DATE_RE = re.compile(r"(\d{1,2})(?:st|nd|rd|th)\s+([A-Za-z]+)\s+(\d{4})")
TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def _schedule_now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _format_gmt_offset_label(offset_minutes: int) -> str:
    sign = "+" if offset_minutes >= 0 else "-"
    absolute = abs(offset_minutes)
    hours = absolute // 60
    minutes = absolute % 60
    return f"GMT {sign}{hours:02d}:{minutes:02d}"


def _ordinal(day: int) -> str:
    if 10 <= day % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def _parse_schedule_datetime_utc(day_label: str, time_text: str) -> datetime | None:
    date_match = DAY_LABEL_DATE_RE.search(day_label)
    time_match = TIME_RE.match(time_text)
    if not date_match or not time_match:
        return None

    day = int(date_match.group(1))
    month_name = date_match.group(2)
    year = int(date_match.group(3))
    hour = int(time_match.group(1))
    minute = int(time_match.group(2))

    try:
        month = datetime.strptime(month_name, "%B").month
    except ValueError:
        return None

    try:
        return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
    except ValueError:
        return None


def _should_include_event(scheduled_at_utc: datetime | None, stale_after_minutes: int) -> bool:
    if scheduled_at_utc is None:
        return True
    cutoff = _schedule_now_utc() - timedelta(minutes=stale_after_minutes)
    return scheduled_at_utc >= cutoff


def _display_schedule_values(
    day_label: str,
    time_text: str,
    scheduled_at_utc: datetime | None,
    offset_minutes: int,
) -> tuple[str, str]:
    if scheduled_at_utc is None:
        return day_label, time_text

    offset = timedelta(minutes=offset_minutes)
    display_dt = scheduled_at_utc + offset
    display_day_label = (
        f"{display_dt.strftime('%A')} {_ordinal(display_dt.day)} "
        f"{display_dt.strftime('%B %Y')} - Schedule Time {_format_gmt_offset_label(offset_minutes)}"
    )
    display_time_text = display_dt.strftime("%H:%M")
    return display_day_label, display_time_text


def _build_event_id(day_label: str, category: str, time_text: str, title: str, channel_ids: list[int]) -> str:
    payload = "|".join([
        day_label,
        category,
        time_text,
        title,
        ",".join(str(channel_id) for channel_id in sorted(channel_ids)),
    ])
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f"dlhd:live:{digest}"


def scrape_schedule(channel_index: dict[int, CatalogChannel] | None = None) -> list[LiveEvent]:
    started_at = perf_counter()
    log_event(LOGGER, logging.INFO, "scrape_schedule_start", **proxy_url_fields(settings.BASE_SITE_URL))
    try:
        with build_session() as session:
            response = guarded_get(
                session,
                settings.BASE_SITE_URL,
                operation="scrape_schedule",
                timeout=settings.HTTP_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            response.encoding = response.encoding or "utf-8"
    except requests.RequestException as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        log_event(
            LOGGER,
            logging.WARNING,
            "scrape_schedule_fail",
            status_code=status_code,
            duration_ms=round((perf_counter() - started_at) * 1000),
            **proxy_url_fields(settings.BASE_SITE_URL),
        )
        raise

    soup = BeautifulSoup(response.text, "html.parser")
    events: list[LiveEvent] = []
    ordinal = 0

    for day_block in soup.select("#schedule > .schedule__day"):
        day_title_node = day_block.select_one(".schedule__dayTitle")
        day_label = day_title_node.get_text(" ", strip=True) if day_title_node else "Live Schedule"

        for category_block in day_block.select(".schedule__category"):
            category_node = category_block.select_one(".schedule__catHeader > .card__meta")
            category = category_node.get_text(" ", strip=True) if category_node else "Live"

            for event_block in category_block.select(".schedule__categoryBody > .schedule__event"):
                title_node = event_block.select_one(".schedule__eventTitle")
                time_node = event_block.select_one(".schedule__time")
                title = title_node.get_text(" ", strip=True) if title_node else "Untitled Event"
                raw_time_text = (time_node.get("data-time") if time_node else None) or (
                    time_node.get_text(" ", strip=True) if time_node else ""
                )
                scheduled_at_utc = _parse_schedule_datetime_utc(day_label, raw_time_text)
                links: list[ScheduleChannelLink] = []
                for anchor in event_block.select('.schedule__channels > a[href*="/watch.php?id="]'):
                    href = anchor.get("href") or ""
                    channel_id_raw = parse_qs(urlparse(href).query).get("id", [None])[0]
                    if not channel_id_raw or not channel_id_raw.isdigit():
                        continue
                    channel_id = int(channel_id_raw)
                    channel_name = anchor.get_text(" ", strip=True) or anchor.get("title") or f"Channel {channel_id}"

                    if channel_index and channel_id in channel_index:
                        channel = channel_index[channel_id]
                        country_code = channel.country_code
                        country_label = channel.country_label
                        channel_name = channel.name
                    else:
                        country_code, country_label = classify_channel_country(channel_name, anchor.get("data-ch"))

                    links.append(
                        ScheduleChannelLink(
                            channel_id=channel_id,
                            name=channel_name,
                            country_code=country_code,
                            country_label=country_label,
                        )
                    )

                if not links:
                    continue

                ordinal += 1
                country_codes = classify_event_countries([link.country_code for link in links])
                events.append(
                    LiveEvent(
                        meta_id=_build_event_id(day_label, category, raw_time_text, title, [link.channel_id for link in links]),
                        title=title,
                        time_text=raw_time_text,
                        day_label=day_label,
                        category=category,
                        channels=links,
                        country_codes=country_codes,
                        ordinal=ordinal,
                        scheduled_at_utc=scheduled_at_utc,
                    )
                )

    log_event(
        LOGGER,
        logging.INFO,
        "scrape_schedule_end",
        event_count=len(events),
        duration_ms=round((perf_counter() - started_at) * 1000),
        **proxy_url_fields(settings.BASE_SITE_URL),
    )
    return events


def filter_schedule(
    events: list[LiveEvent],
    *,
    country_code: str | None,
    search: str | None,
    skip: int,
    stale_after_minutes: int,
) -> list[LiveEvent]:
    items = [event for event in events if _should_include_event(event.scheduled_at_utc, stale_after_minutes)]
    filtered_count = len(events) - len(items)
    if filtered_count:
        log_event(
            LOGGER,
            logging.DEBUG,
            "schedule_event_filtered_stale",
            count=filtered_count,
            stale_after_minutes=stale_after_minutes,
        )
    if country_code:
        items = [event for event in items if country_code in event.country_codes]

    if search:
        needle = search.casefold()
        items = [
            event for event in items
            if needle in event.title.casefold()
            or needle in event.category.casefold()
            or any(needle in channel.name.casefold() for channel in event.channels)
        ]

    return items[skip:skip + settings.CATALOG_PAGE_SIZE]
