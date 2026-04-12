from __future__ import annotations

import hashlib
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from app import settings
from app.http import build_session
from app.models import CatalogChannel, LiveEvent, ScheduleChannelLink
from app.normalize.country import classify_channel_country, classify_event_countries


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
    with build_session() as session:
        response = session.get(settings.BASE_SITE_URL, timeout=settings.HTTP_TIMEOUT_SECONDS)
        response.raise_for_status()
        response.encoding = response.encoding or "utf-8"

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
                time_text = (time_node.get("data-time") if time_node else None) or (
                    time_node.get_text(" ", strip=True) if time_node else ""
                )

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
                        meta_id=_build_event_id(day_label, category, time_text, title, [link.channel_id for link in links]),
                        title=title,
                        time_text=time_text,
                        day_label=day_label,
                        category=category,
                        channels=links,
                        country_codes=country_codes,
                        ordinal=ordinal,
                    )
                )

    return events


def filter_schedule(
    events: list[LiveEvent],
    *,
    country_code: str | None,
    search: str | None,
    skip: int,
) -> list[LiveEvent]:
    items = events
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
