from __future__ import annotations

from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from app import settings
from app.http import build_session
from app.models import CatalogChannel
from app.normalize.country import classify_channel_country


def scrape_channels() -> list[CatalogChannel]:
    with build_session() as session:
        response = session.get(
            f"{settings.BASE_SITE_URL}/24-7-channels.php",
            timeout=settings.HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        response.encoding = response.encoding or "utf-8"

    soup = BeautifulSoup(response.text, "html.parser")
    channels: list[CatalogChannel] = []

    for anchor in soup.select('.grid > a.card[href*="/watch.php?id="]'):
        href = anchor.get("href") or ""
        query = parse_qs(urlparse(href).query)
        channel_id_raw = query.get("id", [None])[0]
        if not channel_id_raw or not channel_id_raw.isdigit():
            continue

        title_node = anchor.select_one(".card__title")
        name = (title_node.get_text(" ", strip=True) if title_node else anchor.get_text(" ", strip=True)).strip()
        search_hint = anchor.get("data-title")
        group_letter = anchor.get("data-first")
        country_code, country_label = classify_channel_country(name, search_hint)

        channels.append(
            CatalogChannel(
                channel_id=int(channel_id_raw),
                name=name,
                watch_url=urljoin(settings.BASE_SITE_URL, href),
                search_hint=search_hint,
                group_letter=group_letter,
                country_code=country_code,
                country_label=country_label,
            )
        )

    return channels


def filter_channels(
    channels: list[CatalogChannel],
    *,
    country_code: str | None,
    search: str | None,
    skip: int,
) -> list[CatalogChannel]:
    items = channels
    if country_code:
        items = [channel for channel in items if channel.country_code == country_code]

    if search:
        needle = search.casefold()
        items = [
            channel for channel in items
            if needle in channel.name.casefold()
            or needle in (channel.search_hint or "").casefold()
            or needle in str(channel.channel_id)
        ]

    return items[skip:skip + settings.CATALOG_PAGE_SIZE]
