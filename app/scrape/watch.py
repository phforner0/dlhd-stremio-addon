from __future__ import annotations

import logging
import re
from time import perf_counter
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup
import requests

from app import settings
from app.http import build_session
from app.logging_utils import log_event, proxy_url_fields
from app.models import (
    AlternatePlayer,
    ChannelInfo,
    PageInfo,
    PlayerInfo,
    PrimaryPlayer,
    RelatedChannel,
    RelatedInfo,
    SourceInfo,
    WrapperCatalog,
)

LOGGER = logging.getLogger("dlhd.scrape.watch")


class WatchFetchError(Exception):
    def __init__(self, channel_id: int, status_code: int | None, message: str) -> None:
        super().__init__(message)
        self.channel_id = channel_id
        self.status_code = status_code

SELECTORS = {
    "description_meta": 'meta[name="description"]',
    "canonical": 'link[rel="canonical"]',
    "og_image": 'meta[property="og:image"]',
    "twitter_image": 'meta[name="twitter:image"]',
    "heading": "main h2, h2",
    "player_frame": "iframe#playerFrame",
    "alternate_buttons": "#playerBtns .player-btn[data-url]",
    "embed_code": "textarea#embedCode",
    "group_labels": ".watch__group label",
    "related_channels": ".watch__chans a[href]",
}


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned or None


def _safe_attr(element, attr: str) -> str | None:
    return _clean(element.get(attr)) if element and element.has_attr(attr) else None


def _abs(url: str | None, base: str | None) -> str | None:
    if not url:
        return None
    return urljoin(base, url.strip()) if base else url.strip()


def _infer_channel_id(canonical: str | None, heading: str | None) -> int | None:
    if canonical:
        query = parse_qs(urlparse(canonical).query)
        if "id" in query and query["id"] and query["id"][0].isdigit():
            return int(query["id"][0])

    if heading:
        match = re.search(r"\(ID\s*(\d+)\)", heading, re.I)
        if match:
            return int(match.group(1))

    return None


def parse_wrapper(html: str, base_url: str | None = None) -> WrapperCatalog:
    soup = BeautifulSoup(html, "html.parser")

    title = _clean(soup.title.get_text(" ", strip=True)) if soup.title else None
    description = _safe_attr(soup.select_one(SELECTORS["description_meta"]), "content")
    canonical_url = _abs(_safe_attr(soup.select_one(SELECTORS["canonical"]), "href"), base_url)
    poster = _abs(
        _safe_attr(soup.select_one(SELECTORS["og_image"]), "content")
        or _safe_attr(soup.select_one(SELECTORS["twitter_image"]), "content"),
        base_url,
    )

    heading_el = soup.select_one(SELECTORS["heading"])
    heading = _clean(heading_el.get_text(" ", strip=True)) if heading_el else None
    channel_id = _infer_channel_id(canonical_url, heading)
    channel_name = re.sub(r"\s*\(ID\s*\d+\)\s*$", "", heading or "", flags=re.I).strip() or None

    player_frame = soup.select_one(SELECTORS["player_frame"])
    primary_src = _abs(_safe_attr(player_frame, "src"), base_url)

    alternates: list[AlternatePlayer] = []
    seen_alternate_urls: set[str] = set()
    for button in soup.select(SELECTORS["alternate_buttons"]):
        url = _abs(_safe_attr(button, "data-url"), base_url)
        label = _clean(button.get_text(" ", strip=True))
        if not url or url in seen_alternate_urls:
            continue
        seen_alternate_urls.add(url)
        alternates.append(
            AlternatePlayer(
                label=label,
                url=url,
                active="is-active" in (button.get("class") or []),
            )
        )

    embed_el = soup.select_one(SELECTORS["embed_code"])

    quick_switch_label = None
    for label_el in soup.select(SELECTORS["group_labels"]):
        text = _clean(label_el.get_text(" ", strip=True))
        if text and "quick switch" in text.lower():
            quick_switch_label = text
            break

    related_channels = [
        RelatedChannel(
            name=_clean(anchor.get_text(" ", strip=True)),
            title=_safe_attr(anchor, "title"),
            url=_abs(_safe_attr(anchor, "href"), base_url),
        )
        for anchor in soup.select(SELECTORS["related_channels"])
    ]

    return WrapperCatalog(
        source=SourceInfo(input=base_url, type="url" if base_url else "file"),
        channel=ChannelInfo(id=channel_id, name=channel_name, heading=heading),
        page=PageInfo(
            title=title,
            description=description,
            canonicalUrl=canonical_url,
            poster=poster,
        ),
        player=PlayerInfo(
            primary=PrimaryPlayer(label="primary", url=primary_src),
            alternates=alternates,
            embedHtml=embed_el.get_text() if embed_el else None,
        ),
        related=RelatedInfo(label=quick_switch_label, channels=related_channels),
    )


def fetch_wrapper(channel_id: int) -> WrapperCatalog:
    started_at = perf_counter()
    url = f"{settings.BASE_SITE_URL}/watch.php?id={channel_id}"
    log_event(LOGGER, logging.INFO, "watch_fetch_start", channel_id=channel_id, **proxy_url_fields(url))
    try:
        with build_session() as session:
            response = session.get(url, timeout=settings.HTTP_TIMEOUT_SECONDS, allow_redirects=True)
            response.raise_for_status()
            response.encoding = response.encoding or "utf-8"
            wrapper = parse_wrapper(response.text, response.url)
            log_event(
                LOGGER,
                logging.INFO,
                "watch_fetch_end",
                channel_id=channel_id,
                status_code=response.status_code,
                duration_ms=round((perf_counter() - started_at) * 1000),
                **proxy_url_fields(response.url),
            )
            return wrapper
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        log_event(
            LOGGER,
            logging.WARNING,
            "watch_fetch_fail",
            channel_id=channel_id,
            status_code=status_code,
            duration_ms=round((perf_counter() - started_at) * 1000),
            **proxy_url_fields(url),
        )
        raise WatchFetchError(channel_id, status_code, f"watch fetch failed for channel {channel_id}") from exc
    except requests.RequestException as exc:
        log_event(
            LOGGER,
            logging.WARNING,
            "watch_fetch_fail",
            channel_id=channel_id,
            duration_ms=round((perf_counter() - started_at) * 1000),
            reason=exc.__class__.__name__,
            **proxy_url_fields(url),
        )
        raise WatchFetchError(channel_id, None, f"watch fetch failed for channel {channel_id}") from exc
