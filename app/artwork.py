from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import re
from textwrap import wrap
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
EVENT_BADGE_SVG_CACHE: TTLCache[str | None] = TTLCache(max_entries=settings.ARTWORK_CACHE_MAX_ENTRIES, name="event_badge_svg")
TEAM_ARTWORK_CACHE: TTLCache[tuple[str | None, str | None]] = TTLCache(max_entries=settings.ARTWORK_CACHE_MAX_ENTRIES, name="team_artwork")
IMAGE_BINARY_CACHE: TTLCache[tuple[bytes, str]] = TTLCache(max_entries=settings.ARTWORK_IMAGE_CACHE_MAX_ENTRIES, name="artwork_image")
PLACEHOLDER_POSTER_PATHS = {"/assets/logos/logo.png"}
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
    return parsed.path.lower() in PLACEHOLDER_POSTER_PATHS


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

        return ArtworkResolution(poster_url=poster_url, background_url=poster_url, source="watch_page")

    return CHANNEL_ARTWORK_CACHE.remember(cache_key, settings.ARTWORK_CACHE_TTL_SECONDS, factory)


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
            _normalized_remote_url(payload.get("strThumb")),
            _normalized_remote_url(payload.get("strPoster")),
            _normalized_remote_url(payload.get("strFanart")),
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

    return TEAM_ARTWORK_CACHE.remember(cache_key, settings.ARTWORK_CACHE_TTL_SECONDS, factory)


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


def _compose_event_badge_svg(event: LiveEvent, left_name: str, right_name: str, left_badge: str | None, right_badge: str | None) -> str | None:
    left_data = _image_data_uri(left_badge) if left_badge else None
    right_data = _image_data_uri(right_badge) if right_badge else None
    if not left_data and not right_data:
        return None

    accent_code = event.country_codes[0] if event.country_codes else "global"
    accent = settings.COUNTRY_COLORS.get(accent_code, settings.COUNTRY_COLORS["global"])
    league_hint = _event_league_hint(event) or event.category
    subtitle = f"{event.day_label} | {event.time_text} | {league_hint}"
    title_lines = _wrapped_lines(event.title, width=22, max_lines=3)
    subtitle_lines = _wrapped_lines(subtitle, width=34, max_lines=2)
    left_name_lines = _wrapped_lines(left_name, width=14, max_lines=2)
    right_name_lines = _wrapped_lines(right_name, width=14, max_lines=2)
    left_image = f'<image href="{left_data}" x="86" y="278" width="164" height="164" preserveAspectRatio="xMidYMid meet"/>' if left_data else ""
    right_image = f'<image href="{right_data}" x="350" y="278" width="164" height="164" preserveAspectRatio="xMidYMid meet"/>' if right_data else ""

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="600" height="900" viewBox="0 0 600 900" role="img" aria-label="{escape(event.title)}">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#0f172a"/>
      <stop offset="100%" stop-color="#020617"/>
    </linearGradient>
  </defs>
  <rect width="600" height="900" fill="url(#bg)"/>
  <rect x="38" y="38" width="524" height="824" rx="34" fill="#08111f" stroke="#1e293b" stroke-width="2"/>
  <rect x="38" y="38" width="524" height="18" rx="9" fill="{accent}"/>
  <rect x="78" y="240" width="180" height="214" rx="28" fill="#0f172a" stroke="#243041"/>
  <rect x="342" y="240" width="180" height="214" rx="28" fill="#0f172a" stroke="#243041"/>
  {left_image}
  {right_image}
  {_svg_text(left_name_lines, x=92, start_y=492, line_height=26, font_size=22, fill="#e2e8f0", weight=700)}
  {_svg_text(right_name_lines, x=356, start_y=492, line_height=26, font_size=22, fill="#e2e8f0", weight=700)}
  {_svg_text(title_lines, x=72, start_y=128, line_height=54, font_size=44, fill="#f8fafc", weight=700)}
  <rect x="72" y="584" width="456" height="2" rx="1" fill="#243041"/>
  {_svg_text(subtitle_lines, x=72, start_y=636, line_height=30, font_size=24, fill="#cbd5e1", weight=500)}
  <text x="72" y="824" fill="#64748b" font-size="18" font-weight="600" font-family="Arial, Helvetica, sans-serif">EVENT ARTWORK FALLBACK</text>
</svg>"""


def resolve_event_badge_svg(event: LiveEvent) -> str | None:
    cache_key = f"event-badge:{event.meta_id}"

    def factory() -> str | None:
        matchup = _event_matchup(event)
        if matchup is None:
            return None

        sport_hint = _event_sport_hint(event)
        left_name, right_name = matchup
        try:
            left_badge, _ = _best_team_artwork(left_name, sport_hint)
            right_badge, _ = _best_team_artwork(right_name, sport_hint)
        except Exception as exc:  # noqa: BLE001
            log_event(
                LOGGER,
                logging.DEBUG,
                "event_badge_artwork_failed",
                event_id=event.meta_id,
                reason=exc.__class__.__name__,
            )
            return None

        return _compose_event_badge_svg(event, left_name, right_name, left_badge, right_badge)

    return EVENT_BADGE_SVG_CACHE.remember(cache_key, settings.ARTWORK_CACHE_TTL_SECONDS, factory)


def resolve_event_artwork(event: LiveEvent) -> ArtworkResolution:
    cache_key = f"event:{event.meta_id}"

    def factory() -> ArtworkResolution:
        if not settings.ARTWORK_ENABLED or settings.ARTWORK_EVENTS_PROVIDER != "thesportsdb":
            return ArtworkResolution(None, None, "disabled")

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

        if best_match is None or best_match[0] < 70:
            return ArtworkResolution(None, None, "svg")

        poster_url, background_url = _event_artwork_urls(best_match[1])
        if not _artwork_url_allowed(poster_url) and not _artwork_url_allowed(background_url):
            return ArtworkResolution(None, None, "svg")

        return ArtworkResolution(
            poster_url=poster_url if _artwork_url_allowed(poster_url) else background_url,
            background_url=background_url if _artwork_url_allowed(background_url) else poster_url,
            source="thesportsdb",
        )

    return EVENT_ARTWORK_CACHE.remember(cache_key, settings.ARTWORK_CACHE_TTL_SECONDS, factory)


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
