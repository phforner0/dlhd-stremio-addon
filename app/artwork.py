from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import re
from textwrap import wrap
from typing import TypeVar
import unicodedata
from urllib.parse import urljoin, urlparse
from xml.sax.saxutils import escape

from app import settings
from app.cache import TTLCache
from app.http import build_session
from app.logging_utils import hash_url, log_event
from app.models import CatalogChannel, LiveEvent, WrapperCatalog
from app.upstream_health import guarded_get

LOGGER = logging.getLogger("dlhd.artwork")
IMAGE_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/svg+xml"}
CHANNEL_ARTWORK_CACHE: TTLCache["ArtworkResolution"] = TTLCache(max_entries=settings.ARTWORK_CACHE_MAX_ENTRIES, name="channel_artwork")
EVENT_ARTWORK_CACHE: TTLCache["ArtworkResolution"] = TTLCache(max_entries=settings.ARTWORK_CACHE_MAX_ENTRIES, name="event_artwork")
EVENT_BADGE_SVG_CACHE: TTLCache["_OptionalValue"] = TTLCache(max_entries=settings.ARTWORK_CACHE_MAX_ENTRIES, name="event_badge_svg")
TEAM_ARTWORK_CACHE: TTLCache["_OptionalValue"] = TTLCache(max_entries=settings.ARTWORK_CACHE_MAX_ENTRIES, name="team_artwork")
IMAGE_BINARY_CACHE: TTLCache[tuple[bytes, str]] = TTLCache(max_entries=settings.ARTWORK_IMAGE_CACHE_MAX_ENTRIES, name="artwork_image")
PLACEHOLDER_POSTER_PATHS = {"/assets/logos/logo.png"}
PLACEHOLDER_POSTER_RE = re.compile(r"/(?:logo|default|placeholder)(?:\.[a-z0-9]+)?$", re.I)
PLACEHOLDER_POSTER_HOSTS = {"dlstreams.com", "www.dlstreams.com", "dlstreams.top", "www.dlstreams.top"}
VS_RE = re.compile(r"\b(vs\.?|v\.?|x)\b", re.I)
SPORT_ALIASES = {
    "football": "soccer",
    "soccer": "soccer",
    "basketball": "basketball",
    "tennis": "tennis",
    "golf": "golf",
    "boxing": "boxing",
    "mma": "mixed martial arts",
    "wrestling": "wrestling",
    "baseball": "baseball",
    "ice hockey": "ice hockey",
    "hockey": "ice hockey",
    "cricket": "cricket",
    "rugby": "rugby",
    "motorsport": "motorsport",
}


@dataclass(slots=True)
class ArtworkResolution:
    poster_url: str | None
    background_url: str | None
    source: str


@dataclass(slots=True)
class _OptionalValue:
    value: object


_T = TypeVar("_T")


def _remember_cached_artwork(cache: TTLCache[ArtworkResolution], key: str, factory: Callable[[], ArtworkResolution]) -> ArtworkResolution:
    cached = cache.get(key)
    if cached is not None:
        return cached
    resolution = factory()
    ttl = settings.ARTWORK_CACHE_TTL_SECONDS if (resolution.poster_url or resolution.background_url) else settings.ARTWORK_MISS_CACHE_TTL_SECONDS
    cache.set(key, resolution, ttl)
    return resolution


def _remember_cached_optional(cache: TTLCache[_OptionalValue], key: str, factory: Callable[[], _T | None]) -> _T | None:
    cached = cache.get(key)
    if cached is not None:
        return cached.value  # type: ignore[return-value]
    value = factory()
    ttl = settings.ARTWORK_CACHE_TTL_SECONDS if value else settings.ARTWORK_MISS_CACHE_TTL_SECONDS
    cache.set(key, _OptionalValue(value), ttl)
    return value


def _host_matches(host: str, allowed: str) -> bool:
    lowered = host.lower()
    if allowed.startswith("."):
        return lowered == allowed[1:] or lowered.endswith(allowed)
    return lowered == allowed


def _host_allowed(host: str) -> bool:
    return any(_host_matches(host, allowed) for allowed in settings.ARTWORK_ALLOWED_HOSTS)


def _normalized_remote_url(url: str | None, *, base_url: str | None = None) -> str | None:
    if not url:
        return None
    resolved = urljoin(base_url, url.strip()) if base_url else url.strip()
    if resolved.startswith("//"):
        resolved = f"https:{resolved}"
    parsed = urlparse(resolved)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return resolved


def _placeholder_channel_poster(url: str | None) -> bool:
    if not url:
        return True
    parsed = urlparse(url)
    path = parsed.path.lower()
    host = (parsed.hostname or "").lower()
    if path in PLACEHOLDER_POSTER_PATHS:
        return True
    if host in PLACEHOLDER_POSTER_HOSTS and path.startswith("/assets/logos/") and PLACEHOLDER_POSTER_RE.search(path):
        return True
    return False


def _artwork_url_allowed(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and _host_allowed(parsed.hostname)


def _first_non_empty(values: list[str | None]) -> str | None:
    for value in values:
        if value:
            return value
    return None


def resolve_channel_artwork(channel: CatalogChannel, wrapper_fetcher) -> ArtworkResolution:
    cache_key = f"channel:{channel.channel_id}"

    def factory() -> ArtworkResolution:
        if not settings.ARTWORK_ENABLED or not settings.ARTWORK_CHANNELS_UPSTREAM_ENABLED:
            return ArtworkResolution(None, None, "disabled")

        try:
            wrapper = wrapper_fetcher()
        except Exception as exc:  # noqa: BLE001
            log_event(
                LOGGER,
                logging.DEBUG,
                "channel_artwork_failed",
                channel_id=channel.channel_id,
                reason=exc.__class__.__name__,
            )
            return ArtworkResolution(None, None, "fetch_failed")

        if not isinstance(wrapper, WrapperCatalog):
            return ArtworkResolution(None, None, "missing_wrapper")

        poster_url = _normalized_remote_url(wrapper.page.poster, base_url=wrapper.page.canonicalUrl)
        if _placeholder_channel_poster(poster_url) or not _artwork_url_allowed(poster_url):
            return ArtworkResolution(None, None, "svg")

        return ArtworkResolution(poster_url=poster_url, background_url=None, source="watch_page")

    return _remember_cached_artwork(CHANNEL_ARTWORK_CACHE, cache_key, factory)


def _normalize_event_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    normalized = normalized.lower().replace("&", " and ")
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"[^a-z0-9 ]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _event_search_queries(event: LiveEvent) -> list[str]:
    title = event.title.strip()
    queries: list[str] = []

    def add(candidate: str | None) -> None:
        if not candidate:
            return
        cleaned = candidate.strip(" -,:;")
        if cleaned and cleaned not in queries:
            queries.append(cleaned)

    add(title)
    if " : " in title:
        add(title.split(" : ", 1)[1])
    if "," in title:
        add(title.split(",", 1)[0])
    if "(" in title:
        add(re.sub(r"\([^)]*\)", "", title))
    vs_match = VS_RE.search(title)
    if vs_match:
        start = title[: vs_match.start()].rsplit(":", 1)[-1].strip(" -")
        end = title[vs_match.end() :].split(",", 1)[0].strip(" -")
        add(f"{start} vs {end}")

    return queries[:4]


def _event_matchup(event: LiveEvent) -> tuple[str, str] | None:
    title = event.title.strip()
    vs_match = VS_RE.search(title)
    if not vs_match:
        return None
    left = title[: vs_match.start()].rsplit(":", 1)[-1].strip(" -")
    right = title[vs_match.end() :].split(",", 1)[0].strip(" -")
    left = re.sub(r"\([^)]*\)", "", left).strip()
    right = re.sub(r"\([^)]*\)", "", right).strip()
    if not left or not right:
        return None
    return left, right


def _event_league_hint(event: LiveEvent) -> str | None:
    title = event.title.strip()
    if ":" not in title:
        return None
    prefix = title.split(":", 1)[0].strip(" -")
    return prefix or None


def _event_sport_hint(event: LiveEvent) -> str | None:
    normalized = _normalize_event_text(event.category)
    return SPORT_ALIASES.get(normalized)


def _parse_candidate_datetime(payload: dict) -> datetime | None:
    timestamp = payload.get("strTimestamp")
    if isinstance(timestamp, str) and timestamp:
        try:
            return datetime.fromisoformat(timestamp).replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    date_event = payload.get("dateEvent")
    time_text = payload.get("strTime") or "00:00:00"
    if isinstance(date_event, str) and date_event:
        try:
            return datetime.fromisoformat(f"{date_event}T{time_text}").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _candidate_has_artwork(payload: dict) -> bool:
    return any(payload.get(key) for key in ("strSquare", "strPoster", "strThumb", "strBanner", "strFanart"))


def _candidate_score(event: LiveEvent, query: str, payload: dict) -> int:
    score = 0
    normalized_query = _normalize_event_text(query)
    normalized_event = _normalize_event_text(event.title)
    normalized_candidate = _normalize_event_text(str(payload.get("strEvent") or ""))
    matchup = _event_matchup(event)
    league_hint = _normalize_event_text(_event_league_hint(event) or "")

    if normalized_candidate == normalized_query:
        score += 90
    elif normalized_query and normalized_query in normalized_candidate:
        score += 50

    if normalized_candidate == normalized_event:
        score += 40
    elif normalized_event and normalized_event in normalized_candidate:
        score += 20

    if _candidate_has_artwork(payload):
        score += 15

    if event.scheduled_at_utc is not None:
        candidate_dt = _parse_candidate_datetime(payload)
        if candidate_dt is not None:
            delta = abs(candidate_dt - event.scheduled_at_utc)
            if delta <= timedelta(hours=12):
                score += 40
            elif delta <= timedelta(days=1):
                score += 15

    sport_hint = _event_sport_hint(event)
    candidate_sport = _normalize_event_text(str(payload.get("strSport") or ""))
    if sport_hint and candidate_sport == sport_hint:
        score += 20

    if league_hint:
        candidate_league = _normalize_event_text(str(payload.get("strLeague") or ""))
        if candidate_league == league_hint:
            score += 30
        elif league_hint and league_hint in candidate_league:
            score += 10

    if matchup is not None:
        home_hint, away_hint = matchup
        home_candidate = _normalize_event_text(str(payload.get("strHomeTeam") or ""))
        away_candidate = _normalize_event_text(str(payload.get("strAwayTeam") or ""))
        normalized_home = _normalize_event_text(home_hint)
        normalized_away = _normalize_event_text(away_hint)
        if normalized_home and home_candidate == normalized_home:
            score += 35
        if normalized_away and away_candidate == normalized_away:
            score += 35
        if normalized_home and normalized_home in normalized_candidate:
            score += 10
        if normalized_away and normalized_away in normalized_candidate:
            score += 10

    return score


def _event_artwork_urls(payload: dict) -> tuple[str | None, str | None]:
    poster_url = _first_non_empty(
        [
            _normalized_remote_url(payload.get("strSquare")),
            _normalized_remote_url(payload.get("strPoster")),
            _normalized_remote_url(payload.get("strThumb")),
        ]
    )
    background_url = _first_non_empty(
        [
            _normalized_remote_url(payload.get("strBanner")),
            _normalized_remote_url(payload.get("strFanart")),
            _normalized_remote_url(payload.get("strThumb")),
        ]
    )
    return poster_url, background_url


def _search_event_artwork(query: str) -> list[dict]:
    api_key = settings.THE_SPORTS_DB_API_KEY
    url = f"https://www.thesportsdb.com/api/v1/json/{api_key}/searchevents.php"
    with build_session() as session:
        response = guarded_get(session, url, operation="artwork_fetch", timeout=settings.HTTP_TIMEOUT_SECONDS, params={"e": query})
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        return []
    events = payload.get("event")
    return events if isinstance(events, list) else []


def _best_event_candidate(event: LiveEvent, *, minimum_score: int) -> tuple[int, dict] | None:
    best_match: tuple[int, dict] | None = None
    for query in _event_search_queries(event):
        try:
            candidates = _search_event_artwork(query)
        except Exception as exc:  # noqa: BLE001
            log_event(
                LOGGER,
                logging.DEBUG,
                "event_artwork_search_failed",
                event_id=event.meta_id,
                query=query,
                reason=exc.__class__.__name__,
            )
            continue

        for candidate in candidates:
            score = _candidate_score(event, query, candidate)
            if best_match is None or score > best_match[0]:
                best_match = (score, candidate)

    if best_match is None or best_match[0] < minimum_score:
        return None
    return best_match


def _search_team_artwork(query: str) -> list[dict]:
    api_key = settings.THE_SPORTS_DB_API_KEY
    url = f"https://www.thesportsdb.com/api/v1/json/{api_key}/searchteams.php"
    with build_session() as session:
        response = guarded_get(session, url, operation="artwork_fetch", timeout=settings.HTTP_TIMEOUT_SECONDS, params={"t": query})
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        return []
    teams = payload.get("teams")
    return teams if isinstance(teams, list) else []


def _team_candidate_score(team_name: str, sport_hint: str | None, payload: dict) -> int:
    normalized_team = _normalize_event_text(team_name)
    normalized_candidate = _normalize_event_text(str(payload.get("strTeam") or ""))
    alternates = _normalize_event_text(str(payload.get("strTeamAlternate") or ""))
    score = 0

    if normalized_candidate == normalized_team:
        score += 90
    elif normalized_team and normalized_team in normalized_candidate:
        score += 40

    if normalized_team and normalized_team in alternates:
        score += 30

    candidate_sport = _normalize_event_text(str(payload.get("strSport") or ""))
    if sport_hint and candidate_sport == sport_hint:
        score += 20

    if payload.get("strBadge"):
        score += 10
    return score


def _best_team_artwork(team_name: str, sport_hint: str | None) -> tuple[str | None, str | None]:
    cache_key = f"team:{_normalize_event_text(team_name)}:{sport_hint or 'any'}"

    def factory() -> tuple[str | None, str | None]:
        best_match: tuple[int, dict] | None = None
        for candidate in _search_team_artwork(team_name):
            score = _team_candidate_score(team_name, sport_hint, candidate)
            if best_match is None or score > best_match[0]:
                best_match = (score, candidate)

        if best_match is None or best_match[0] < 60:
            return (None, None)

        payload = best_match[1]
        badge_url = _normalized_remote_url(payload.get("strBadge"))
        fanart_url = _first_non_empty(
            [
                _normalized_remote_url(payload.get("strFanart1")),
                _normalized_remote_url(payload.get("strFanart2")),
                _normalized_remote_url(payload.get("strFanart3")),
                _normalized_remote_url(payload.get("strBanner")),
            ]
        )
        return (
            badge_url if _artwork_url_allowed(badge_url) else None,
            fanart_url if _artwork_url_allowed(fanart_url) else None,
        )

    return _remember_cached_optional(TEAM_ARTWORK_CACHE, cache_key, factory) or (None, None)


def _image_data_uri(url: str | None) -> str | None:
    if not url:
        return None
    body, media_type = fetch_artwork_binary(url)
    return f"data:{media_type};base64,{base64.b64encode(body).decode('ascii')}"


def _wrapped_lines(text: str, *, width: int, max_lines: int) -> list[str]:
    normalized = " ".join((text or "").split())
    if not normalized:
        return []
    lines = wrap(normalized, width=width, break_long_words=True, break_on_hyphens=False)
    if len(lines) <= max_lines:
        return lines
    truncated = lines[: max_lines - 1]
    remainder = " ".join(lines[max_lines - 1 :])
    truncated.append(remainder[: width - 3].rstrip() + "...")
    return truncated


def _svg_text(lines: list[str], *, x: int, start_y: int, line_height: int, font_size: int, fill: str, weight: int = 700) -> str:
    if not lines:
        return ""
    tspans = [f'<tspan x="{x}" y="{start_y + (idx * line_height)}">{escape(line)}</tspan>' for idx, line in enumerate(lines)]
    return (
        f'<text fill="{fill}" font-size="{font_size}" font-weight="{weight}" '
        f'font-family="Arial, Helvetica, sans-serif">{"".join(tspans)}</text>'
    )


def _compose_event_badge_svg(event: LiveEvent, badges: list[tuple[str, str]], *, aspect: str) -> str | None:
    if not badges:
        return None

    accent_code = event.country_codes[0] if event.country_codes else "global"
    accent = settings.COUNTRY_COLORS.get(accent_code, settings.COUNTRY_COLORS["global"])
    league_hint = _event_league_hint(event) or event.category
    subtitle = f"{event.day_label} | {event.time_text} | {league_hint}"

    if aspect == "background":
        width = 1600
        height = 900
        view_box = "0 0 1600 900"
        title_lines = _wrapped_lines(event.title, width=28, max_lines=3)
        subtitle_lines = _wrapped_lines(subtitle, width=68, max_lines=2)
        title_block = _svg_text(title_lines, x=96, start_y=160, line_height=72, font_size=58, fill="#f8fafc", weight=700)
        subtitle_block = _svg_text(subtitle_lines, x=96, start_y=414, line_height=38, font_size=30, fill="#dbe4f0", weight=500)
        image_y = 518
        slots = [(370, 220), (1010, 220)] if len(badges) >= 2 else [(690, 220)]
        name_y = image_y + 284
        footer_y = 812
    else:
        width = 600
        height = 900
        view_box = "0 0 600 900"
        title_lines = _wrapped_lines(event.title, width=22, max_lines=3)
        subtitle_lines = _wrapped_lines(subtitle, width=34, max_lines=2)
        title_block = _svg_text(title_lines, x=72, start_y=128, line_height=54, font_size=44, fill="#f8fafc", weight=700)
        subtitle_block = _svg_text(subtitle_lines, x=72, start_y=636, line_height=30, font_size=24, fill="#cbd5e1", weight=500)
        image_y = 278
        slots = [(78, 164), (342, 164)] if len(badges) >= 2 else [(210, 164)]
        name_y = image_y + 214
        footer_y = 824

    cards = []
    for index, (name, image_data) in enumerate(badges[:2]):
        x, size = slots[min(index, len(slots) - 1)]
        cards.append(f'<rect x="{x}" y="{image_y - 38}" width="{size + 16}" height="{size + 50}" rx="28" fill="#0f172a" stroke="#243041"/>')
        cards.append(f'<image href="{image_data}" x="{x + 8}" y="{image_y}" width="{size}" height="{size}" preserveAspectRatio="xMidYMid meet"/>')
        cards.append(_svg_text(_wrapped_lines(name, width=16 if aspect == "background" else 14, max_lines=2), x=x + 14, start_y=name_y, line_height=26, font_size=22, fill="#e2e8f0", weight=700))

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="{view_box}" role="img" aria-label="{escape(event.title)}">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#0f172a"/>
      <stop offset="100%" stop-color="#020617"/>
    </linearGradient>
  </defs>
  <rect width="{width}" height="{height}" fill="url(#bg)"/>
  <rect x="38" y="38" width="{width - 76}" height="{height - 76}" rx="34" fill="#08111f" stroke="#1e293b" stroke-width="2"/>
  <rect x="38" y="38" width="{width - 76}" height="18" rx="9" fill="{accent}"/>
  {title_block}
  <rect x="72" y="{584 if aspect != 'background' else 362}" width="{456 if aspect != 'background' else 1120}" height="2" rx="1" fill="#243041"/>
  {subtitle_block}
  {''.join(cards)}
  <text x="72" y="{footer_y}" fill="#64748b" font-size="18" font-weight="600" font-family="Arial, Helvetica, sans-serif">EVENT ARTWORK FALLBACK</text>
</svg>"""


def _league_badge_svg(event: LiveEvent, payload: dict, *, aspect: str) -> str | None:
    badge_url = _normalized_remote_url(payload.get("strLeagueBadge"))
    if not _artwork_url_allowed(badge_url):
        return None
    badge_data = _image_data_uri(badge_url)
    if not badge_data:
        return None
    league_name = str(payload.get("strLeague") or event.category or "Live Event")
    return _compose_event_badge_svg(event, [(league_name, badge_data)], aspect=aspect)


def resolve_event_badge_svg(event: LiveEvent, *, aspect: str = "poster") -> str | None:
    cache_key = f"event-badge:{event.meta_id}"
    if aspect != "poster":
        cache_key = f"{cache_key}:{aspect}"

    def factory() -> str | None:
        matchup = _event_matchup(event)
        sport_hint = _event_sport_hint(event)
        if matchup is not None:
            left_name, right_name = matchup
            try:
                left_badge, _ = _best_team_artwork(left_name, sport_hint)
                right_badge, _ = _best_team_artwork(right_name, sport_hint)
                badges: list[tuple[str, str]] = []
                if left_badge:
                    left_data = _image_data_uri(left_badge)
                    if left_data:
                        badges.append((left_name, left_data))
                if right_badge:
                    right_data = _image_data_uri(right_badge)
                    if right_data:
                        badges.append((right_name, right_data))
            except Exception as exc:  # noqa: BLE001
                log_event(
                    LOGGER,
                    logging.DEBUG,
                    "event_badge_artwork_failed",
                    event_id=event.meta_id,
                    reason=exc.__class__.__name__,
                )
                badges = []

            if badges:
                return _compose_event_badge_svg(event, badges, aspect=aspect)

        best_match = _best_event_candidate(event, minimum_score=45)
        if best_match is not None:
            return _league_badge_svg(event, best_match[1], aspect=aspect)
        return None

    return _remember_cached_optional(EVENT_BADGE_SVG_CACHE, cache_key, factory)


def resolve_event_artwork(event: LiveEvent) -> ArtworkResolution:
    cache_key = f"event:{event.meta_id}"

    def factory() -> ArtworkResolution:
        if not settings.ARTWORK_ENABLED or settings.ARTWORK_EVENTS_PROVIDER != "thesportsdb":
            return ArtworkResolution(None, None, "disabled")

        best_match = _best_event_candidate(event, minimum_score=70)
        if best_match is None:
            return ArtworkResolution(None, None, "svg")

        poster_url, background_url = _event_artwork_urls(best_match[1])
        poster_url = poster_url if _artwork_url_allowed(poster_url) else None
        background_url = background_url if _artwork_url_allowed(background_url) else None
        if not poster_url and not background_url:
            return ArtworkResolution(None, None, "svg")

        return ArtworkResolution(
            poster_url=poster_url,
            background_url=background_url,
            source="thesportsdb",
        )

    return _remember_cached_artwork(EVENT_ARTWORK_CACHE, cache_key, factory)


def fetch_artwork_binary(url: str) -> tuple[bytes, str]:
    cache_key = f"image:{hash_url(url)}"
    cached = IMAGE_BINARY_CACHE.get(cache_key)
    if cached is not None:
        return cached

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or not _host_allowed(parsed.hostname):
        raise ValueError("disallowed artwork host")

    with build_session() as session:
        response = guarded_get(session, url, operation="artwork_fetch", timeout=settings.HTTP_TIMEOUT_SECONDS, stream=True, allow_redirects=True)
        response.raise_for_status()
        final_url = response.url
        final_host = urlparse(final_url).hostname
        if not final_host or not _host_allowed(final_host):
            response.close()
            raise ValueError("disallowed redirect artwork host")
        content_type = response.headers.get("Content-Type", "application/octet-stream").split(";", 1)[0].strip().lower()
        if content_type not in IMAGE_CONTENT_TYPES:
            response.close()
            raise ValueError("invalid artwork content type")
        body = response.content
        response.close()

    IMAGE_BINARY_CACHE.set(cache_key, (body, content_type), settings.ARTWORK_IMAGE_CACHE_TTL_SECONDS)
    return body, content_type
