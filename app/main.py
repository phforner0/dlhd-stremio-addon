from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from time import perf_counter
import re
from urllib.parse import quote, unquote, urlencode, urljoin, urlparse
import logging

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.responses import StreamingResponse

from app import settings
from app.cache import TTLCache
from app.http import DEFAULT_HEADERS, build_session, close_pooled_sessions, get_pooled_session
from app.manifest import build_manifest
from app.models import CatalogChannel, LiveEvent, WrapperCatalog
from app.posters import render_svg_poster
from app.resolve.player import PlaywrightResolver
from app.scrape.channels import filter_channels, scrape_channels
from app.scrape.schedule import _display_schedule_values, filter_schedule, scrape_schedule
from app.scrape.watch import fetch_wrapper

app = FastAPI(title=settings.ADDON_NAME, docs_url=None, redoc_url=None)
LOGGER = logging.getLogger("dlhd.addon")
HLS_URI_ATTR_RE = re.compile(r'URI="([^"]+)"')

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "HEAD", "OPTIONS"],
    allow_headers=["*"],
    max_age=86400,
)

channels_cache: TTLCache[list[CatalogChannel]] = TTLCache()
channel_index_cache: TTLCache[dict[int, CatalogChannel]] = TTLCache()
schedule_cache: TTLCache[list[LiveEvent]] = TTLCache()
schedule_index_cache: TTLCache[dict[str, LiveEvent]] = TTLCache()
watch_cache: TTLCache[WrapperCatalog] = TTLCache()
stream_cache: TTLCache[list[dict]] = TTLCache()
live_channel_stream_cache: TTLCache[dict[str, str]] = TTLCache()
playlist_cache: TTLCache[str] = TTLCache()
resolver = PlaywrightResolver()


def _log_timing(message: str, started_at: float, **fields: object) -> None:
    elapsed_ms = round((perf_counter() - started_at) * 1000)
    details = " ".join(f"{key}={value}" for key, value in fields.items())
    if details:
        LOGGER.debug("%s duration_ms=%s %s", message, elapsed_ms, details)
    else:
        LOGGER.debug("%s duration_ms=%s", message, elapsed_ms)


def _service_base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _playlist_cache_key(url: str, referer: str) -> str:
    return f"playlist:{url}|{referer}"


def _looks_like_hls_playlist(body: str) -> bool:
    return body.lstrip().startswith("#EXTM3U")


def _poster_url(request: Request, meta_id: str) -> str:
    return f"{_service_base_url(request)}/assets/poster/{quote(meta_id, safe='')}.svg"


def _parse_extra(extra: str | None) -> dict[str, str]:
    parsed: dict[str, str] = {}
    if not extra:
        return parsed
    for segment in extra.split("/"):
        if not segment or "=" not in segment:
            continue
        key, value = segment.split("=", 1)
        parsed[key] = unquote(value)
    return parsed


def _parse_user_config(config: str | None) -> dict[str, str]:
    if not config:
        return {}

    try:
        parsed = json.loads(config)
    except (json.JSONDecodeError, TypeError):
        return {}

    if not isinstance(parsed, dict):
        return {}

    normalized: dict[str, str] = {}
    for key, value in parsed.items():
        if isinstance(value, (str, int, float, bool)):
            normalized[str(key)] = str(value)
    return normalized


def _schedule_offset_minutes(config: dict[str, str]) -> int:
    raw_value = config.get("scheduleOffsetMin")
    if raw_value is None:
        return settings.SCHEDULE_DISPLAY_GMT_OFFSET_MINUTES

    raw_minutes = raw_value.split("|", 1)[0]
    try:
        return int(raw_minutes)
    except ValueError:
        return settings.SCHEDULE_DISPLAY_GMT_OFFSET_MINUTES


def _int_config_value(config: dict[str, str], key: str, default: int, allowed: set[int]) -> int:
    raw_value = config.get(key)
    if raw_value is None:
        return default

    raw_number = raw_value.split("|", 1)[0]
    try:
        parsed = int(raw_number)
    except ValueError:
        return default
    return parsed if parsed in allowed else default


def _catalog_mode(config: dict[str, str]) -> str:
    return config.get("catalogMode", "full").split("|", 1)[0]


def _preferred_country_code(config: dict[str, str]) -> str | None:
    raw_value = config.get("preferredCountryCode", "all").split("|", 1)[0]
    if raw_value in {"", "all"}:
        return None
    return raw_value if raw_value in settings.COUNTRY_LABELS else None


def _channel_stream_result_limit(config: dict[str, str]) -> int:
    return _int_config_value(config, "channelStreamResults", settings.CHANNEL_STREAM_MAX_RESULTS, {1, 2})


def _live_stream_result_limit(config: dict[str, str]) -> int:
    return _int_config_value(config, "liveStreamResults", settings.LIVE_STREAM_MAX_RESULTS, {1, 2, 4})


def _event_stale_after_minutes(config: dict[str, str]) -> int:
    return _int_config_value(config, "eventStaleAfterMinutes", settings.EVENT_STALE_AFTER_MINUTES, {120, 360, 720})


def _config_fingerprint(config: dict[str, str], keys: tuple[str, ...]) -> str:
    parts = []
    for key in keys:
        if key in config:
            parts.append(f"{key}={config[key]}")
    return "|".join(parts) or "default"


def _event_display_values(event: LiveEvent, config: dict[str, str]) -> tuple[str, str]:
    if event.scheduled_at_utc is None:
        return event.day_label, event.time_text
    return _display_schedule_values(event.day_label, event.time_text, event.scheduled_at_utc, _schedule_offset_minutes(config))


def _configure_page(request: Request) -> str:
    default_offset = settings.SCHEDULE_DISPLAY_GMT_OFFSET_MINUTES
    options = []
    for minutes in range(-12 * 60, 14 * 60 + 1, 30):
        sign = "+" if minutes >= 0 else "-"
        absolute = abs(minutes)
        hours = absolute // 60
        mins = absolute % 60
        selected = " selected" if minutes == default_offset else ""
        label = f"GMT {sign}{hours:02d}:{mins:02d}"
        options.append(f'<option value="{minutes}"{selected}>{label}</option>')

    base_url = str(request.base_url).rstrip("/")
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>{settings.ADDON_NAME} Configure</title>
  <style>
    body {{ font-family: Arial, sans-serif; max-width: 560px; margin: 40px auto; padding: 0 16px; background: #111827; color: #fff; }}
    h1 {{ margin-bottom: 8px; }}
    p {{ color: #d1d5db; line-height: 1.5; }}
    label {{ display: block; margin: 24px 0 8px; font-weight: 700; }}
    select, button {{ width: 100%; padding: 12px; font-size: 16px; border-radius: 8px; border: 1px solid #374151; }}
    select {{ background: #0f172a; color: #fff; }}
    button {{ margin-top: 20px; background: #8b5cf6; color: #fff; cursor: pointer; border: 0; }}
    a {{ color: #93c5fd; }}
  </style>
</head>
<body>
  <h1>{settings.ADDON_NAME}</h1>
  <p>Choose the schedule timezone for this installation. Each user can install the addon with a different offset.</p>
  <label for=\"scheduleOffsetMin\">Schedule Timezone</label>
  <select id=\"scheduleOffsetMin\">{''.join(options)}</select>
  <button id=\"installBtn\">Install in Stremio</button>
  <p id=\"manifestUrl\"></p>
  <script>
    const select = document.getElementById('scheduleOffsetMin');
    const installBtn = document.getElementById('installBtn');
    const manifestUrl = document.getElementById('manifestUrl');
    function currentManifest() {{
      const payload = encodeURIComponent(JSON.stringify({{ scheduleOffsetMin: select.value }}));
      return `{base_url}/${{payload}}/manifest.json`;
    }}
    function refresh() {{
      manifestUrl.textContent = currentManifest();
    }}
    select.addEventListener('change', refresh);
    installBtn.addEventListener('click', () => {{
      window.location.href = currentManifest().replace('https://', 'stremio://').replace('http://', 'stremio://');
    }});
    refresh();
  </script>
</body>
</html>"""


def _configure_page_v2(request: Request) -> str:
    def select_html(field_id: str, title: str, options: list[tuple[str, str]], default_value: str) -> str:
        rendered = []
        for value, label in options:
            selected = " selected" if value == default_value else ""
            rendered.append(f'<option value="{value}"{selected}>{label}</option>')
        return f'<label for="{field_id}">{title}</label><select id="{field_id}">{"".join(rendered)}</select>'

    timezone_options = []
    for minutes in range(-12 * 60, 14 * 60 + 1, 30):
        sign = "+" if minutes >= 0 else "-"
        absolute = abs(minutes)
        hours = absolute // 60
        mins = absolute % 60
        timezone_options.append((str(minutes), f"GMT {sign}{hours:02d}:{mins:02d}"))

    country_options = [("all", "All Countries")]
    for country_code in settings.VISIBLE_COUNTRY_CODES:
        country_options.append((country_code, settings.COUNTRY_LABELS[country_code]))

    fields_html = "".join(
        [
            select_html("scheduleOffsetMin", "Schedule Timezone", timezone_options, str(settings.SCHEDULE_DISPLAY_GMT_OFFSET_MINUTES)),
            select_html("catalogMode", "Catalog Mode", [("full", "All catalogs"), ("focused", "Only all + one country + global")], "full"),
            select_html("preferredCountryCode", "Preferred Country", country_options, "all"),
            select_html("channelStreamResults", "Channel Stream Options", [("1", "Best only"), ("2", "Two options")], str(settings.CHANNEL_STREAM_MAX_RESULTS)),
            select_html("liveStreamResults", "Live Stream Options", [("1", "Best only"), ("2", "Two options"), ("4", "More options")], str(settings.LIVE_STREAM_MAX_RESULTS)),
            select_html("eventStaleAfterMinutes", "Hide Finished Events After", [("120", "2 hours"), ("360", "6 hours"), ("720", "12 hours")], str(settings.EVENT_STALE_AFTER_MINUTES)),
        ]
    )

    base_url = str(request.base_url).rstrip("/")
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>{settings.ADDON_NAME} Configure</title>
  <style>
    body {{ font-family: Arial, sans-serif; max-width: 560px; margin: 40px auto; padding: 0 16px; background: #111827; color: #fff; }}
    h1 {{ margin-bottom: 8px; }}
    p {{ color: #d1d5db; line-height: 1.5; }}
    label {{ display: block; margin: 18px 0 8px; font-weight: 700; }}
    select, button {{ width: 100%; padding: 12px; font-size: 16px; border-radius: 8px; border: 1px solid #374151; }}
    select {{ background: #0f172a; color: #fff; }}
    button {{ margin-top: 20px; background: #8b5cf6; color: #fff; cursor: pointer; border: 0; }}
    a {{ color: #93c5fd; }}
  </style>
</head>
<body>
  <h1>{settings.ADDON_NAME}</h1>
  <p>Choose what appears and how the addon behaves for this installation. Each user can install with different settings.</p>
  {fields_html}
  <button id=\"installBtn\">Install in Stremio</button>
  <p id=\"manifestUrl\"></p>
  <script>
    const fields = ['scheduleOffsetMin', 'catalogMode', 'preferredCountryCode', 'channelStreamResults', 'liveStreamResults', 'eventStaleAfterMinutes']
      .map((id) => document.getElementById(id));
    const installBtn = document.getElementById('installBtn');
    const manifestUrl = document.getElementById('manifestUrl');
    function currentManifest() {{
      const config = Object.fromEntries(fields.map((field) => [field.id, field.value]));
      const payload = encodeURIComponent(JSON.stringify(config));
      return `{base_url}/${{payload}}/manifest.json`;
    }}
    function refresh() {{
      manifestUrl.textContent = currentManifest();
    }}
    fields.forEach((field) => field.addEventListener('change', refresh));
    installBtn.addEventListener('click', () => {{
      window.location.href = currentManifest().replace('https://', 'stremio://').replace('http://', 'stremio://');
    }});
    refresh();
  </script>
</body>
</html>"""


def _find_catalog(catalog_id: str) -> dict:
    catalog = settings.CATALOGS_BY_ID.get(catalog_id)
    if not catalog:
        raise HTTPException(status_code=404, detail="unknown catalog")
    return catalog


def _effective_country_filter(catalog_def: dict, config: dict[str, str]) -> str | None:
    configured_country = _preferred_country_code(config)
    catalog_country = catalog_def["country_code"]
    if catalog_country is not None:
        return catalog_country

    if _catalog_mode(config) == "focused" and configured_country is not None:
        return configured_country

    return None


def get_channels() -> list[CatalogChannel]:
    started_at = perf_counter()
    return channels_cache.remember(
        "channels",
        settings.CHANNELS_CACHE_TTL_SECONDS,
        lambda: _timed_call("channels scrape", scrape_channels, started_at=started_at),
    )


def get_channel_index() -> dict[int, CatalogChannel]:
    return channel_index_cache.remember(
        "channels:index",
        settings.CHANNELS_CACHE_TTL_SECONDS,
        lambda: {channel.channel_id: channel for channel in get_channels()},
    )


def get_cached_channel(channel_id: int) -> CatalogChannel | None:
    channel_index = channel_index_cache.get("channels:index")
    if channel_index is None:
        return None
    return channel_index.get(channel_id)


def refresh_schedule() -> list[LiveEvent]:
    started_at = perf_counter()
    events = scrape_schedule(get_channel_index())
    schedule_cache.set("schedule", events, settings.SCHEDULE_CACHE_TTL_SECONDS)
    schedule_index_cache.set(
        "schedule:index",
        {event.meta_id: event for event in events},
        settings.SCHEDULE_CACHE_TTL_SECONDS,
    )
    _log_timing("schedule refresh", started_at, events=len(events))
    return events


def get_schedule() -> list[LiveEvent]:
    return schedule_cache.remember(
        "schedule",
        settings.SCHEDULE_CACHE_TTL_SECONDS,
        lambda: scrape_schedule(get_channel_index()),
    )


def get_schedule_index() -> dict[str, LiveEvent]:
    return schedule_index_cache.remember(
        "schedule:index",
        settings.SCHEDULE_CACHE_TTL_SECONDS,
        lambda: {event.meta_id: event for event in get_schedule()},
    )


def get_wrapper(channel_id: int) -> WrapperCatalog:
    started_at = perf_counter()
    return watch_cache.remember(
        f"watch:{channel_id}",
        settings.WATCH_CACHE_TTL_SECONDS,
        lambda: _timed_call("watch fetch", lambda: fetch_wrapper(channel_id), started_at=started_at, channel_id=channel_id),
    )


def _country_label(country_code: str) -> str:
    return settings.COUNTRY_LABELS[country_code]


def _ordered_player_targets(wrapper: WrapperCatalog) -> list[tuple[str, str]]:
    targets: list[tuple[str, str]] = []
    seen_urls: set[str] = set()

    for alternate in wrapper.player.alternates:
        if not alternate.active or alternate.url in seen_urls:
            continue
        seen_urls.add(alternate.url)
        targets.append((alternate.label or "alternate", alternate.url))

    if wrapper.player.primary.url and wrapper.player.primary.url not in seen_urls:
        seen_urls.add(wrapper.player.primary.url)
        targets.append((wrapper.player.primary.label, wrapper.player.primary.url))

    for alternate in wrapper.player.alternates:
        if alternate.active or alternate.url in seen_urls:
            continue
        seen_urls.add(alternate.url)
        targets.append((alternate.label or "alternate", alternate.url))

    return targets


def _ordered_live_channels(event: LiveEvent, config: dict[str, str]) -> list:
    unique_channels = list({channel.channel_id: channel for channel in event.channels}.values())
    configured_country = _preferred_country_code(config)
    if configured_country is not None:
        preferred_country_codes = [configured_country]
    else:
        preferred_country_codes = [code for code in event.country_codes if code != "global"]

    def key(channel) -> tuple[int, int, int]:
        cached = 0 if live_channel_stream_cache.get(f"live-channel:{channel.channel_id}") is not None else 1
        if preferred_country_codes:
            if channel.country_code in preferred_country_codes:
                country_weight = 0
            elif channel.country_code != "global":
                country_weight = 1
            else:
                country_weight = 2
        else:
            country_weight = 0 if channel.country_code != "global" else 1

        return (country_weight, cached, channel.channel_id)

    return sorted(unique_channels, key=key)


def _channel_preview(channel: CatalogChannel, request: Request) -> dict:
    return {
        "id": channel.meta_id,
        "type": "tv",
        "name": channel.name,
        "poster": _poster_url(request, channel.meta_id),
        "posterShape": "poster",
        "description": f"{channel.country_label} channel • ID {channel.channel_id}",
        "genres": [channel.country_label],
    }


def _event_preview(event: LiveEvent, request: Request, config: dict[str, str]) -> dict:
    country_labels = [_country_label(code) for code in event.country_codes]
    day_label, time_text = _event_display_values(event, config)
    return {
        "id": event.meta_id,
        "type": "tv",
        "name": event.title,
        "poster": _poster_url(request, event.meta_id),
        "posterShape": "poster",
        "description": f"{day_label} • {time_text} • {event.category}",
        "genres": [event.category, *country_labels],
    }


def _parse_meta_id(meta_id: str) -> tuple[str, int | str]:
    if meta_id.startswith("dlhd:ch:"):
        channel_id = meta_id.removeprefix("dlhd:ch:")
        if channel_id.isdigit():
            return "channel", int(channel_id)
    if meta_id.startswith("dlhd:live:"):
        return "live", meta_id
    raise HTTPException(status_code=404, detail="unknown meta id")


def _manifest_to_stream(
    *,
    request: Request,
    display_name: str,
    description: str,
    manifest_url: str,
    referer: str,
    player_type: str,
) -> dict:
    if player_type.lower() == "hls":
        return {
            "name": display_name,
            "description": description,
            "url": _proxy_media_url(request, manifest_url, referer, filename_hint="stream.m3u8"),
            "behaviorHints": {
                "notWebReady": True,
                "filename": "stream.m3u8",
            },
        }

    filename = manifest_url.rstrip("/").split("/")[-1] or "stream.m3u8"
    return {
        "name": display_name,
        "description": description,
        "url": manifest_url,
        "behaviorHints": {
            "notWebReady": True,
            "filename": filename,
            "proxyHeaders": {
                "request": {
                    "Referer": referer,
                    "User-Agent": DEFAULT_HEADERS["User-Agent"],
                }
            },
        },
    }


def _proxy_media_url(
    request: Request,
    upstream_url: str,
    referer: str,
    *,
    filename_hint: str | None = None,
) -> str:
    parsed = urlparse(upstream_url)
    filename = filename_hint or (parsed.path.rsplit("/", 1)[-1] or "stream.bin")
    safe_filename = quote(filename, safe="._-")
    query = urlencode({"url": upstream_url, "referer": referer})
    return f"{_service_base_url(request)}/proxy/{safe_filename}?{query}"


def _rewrite_playlist_line(request: Request, line: str, playlist_url: str, referer: str) -> str:
    stripped = line.strip()
    if not stripped:
        return line

    if stripped.startswith("#"):
        def repl(match: re.Match[str]) -> str:
            absolute = urljoin(playlist_url, match.group(1))
            return f'URI="{_proxy_media_url(request, absolute, referer)}"'

        return HLS_URI_ATTR_RE.sub(repl, line)

    absolute = urljoin(playlist_url, stripped)
    return _proxy_media_url(request, absolute, referer)


def _rewrite_hls_playlist(request: Request, body: str, playlist_url: str, referer: str) -> str:
    rewritten = [
        _rewrite_playlist_line(request, line, playlist_url, referer)
        for line in body.splitlines()
    ]
    return "\n".join(rewritten) + ("\n" if body.endswith("\n") else "")


def _stream_upstream(upstream):
    try:
        for chunk in upstream.iter_content(chunk_size=256 * 1024):
            if chunk:
                yield chunk
    finally:
        upstream.close()


def _default_video(meta_id: str, title: str) -> dict:
    return {
        "id": meta_id,
        "title": title,
        "released": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "available": True,
    }


def _timed_call(label: str, factory, *, started_at: float | None = None, **fields: object):
    timing_started_at = perf_counter() if started_at is None else started_at
    value = factory()
    _log_timing(label, timing_started_at, **fields)
    return value


def _remember_streams(cache_key: str, factory) -> list[dict]:
    cached = stream_cache.get(cache_key)
    if cached is not None:
        return cached

    streams = factory()
    ttl_seconds = (
        settings.STREAM_CACHE_TTL_SECONDS
        if streams
        else settings.FAILED_STREAM_CACHE_TTL_SECONDS
    )
    return stream_cache.set(cache_key, streams, ttl_seconds)


def _build_channel_streams(channel: CatalogChannel, request: Request, config: dict[str, str]) -> list[dict]:
    def factory() -> list[dict]:
        started_at = perf_counter()
        try:
            wrapper = get_wrapper(channel.channel_id)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Channel stream resolution failed for %s: %s", channel.channel_id, exc)
            return []

        targets = _ordered_player_targets(wrapper)

        streams: list[dict] = []
        seen_urls: set[str] = set()
        for label, player_url in targets[:settings.CHANNEL_STREAM_MAX_ATTEMPTS]:
            try:
                resolution = resolver.resolve_player(label, player_url)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning(
                    "Channel player resolution failed for %s (%s): %s",
                    channel.channel_id,
                    player_url,
                    exc,
                )
                continue

            for manifest in resolution.manifests:
                if manifest.url in seen_urls:
                    continue
                seen_urls.add(manifest.url)
                streams.append(
                    _manifest_to_stream(
                        request=request,
                        display_name=settings.ADDON_NAME,
                        description=(
                            f"{channel.name} • {channel.country_label} • "
                            f"{resolution.label} • {manifest.player_type.upper()}"
                        ),
                        manifest_url=manifest.url,
                        referer=manifest.found_at_url,
                        player_type=manifest.player_type,
                    )
                )
                if len(streams) >= _channel_stream_result_limit(config):
                    return streams

            if streams:
                _log_timing("channel stream resolve", started_at, channel_id=channel.channel_id, streams=len(streams))
                return streams

        _log_timing("channel stream resolve", started_at, channel_id=channel.channel_id, streams=0)
        return streams

    return _remember_streams(
        (
            f"stream:channel:{channel.channel_id}:{_service_base_url(request)}:"
            f"{_config_fingerprint(config, ('channelStreamResults',))}"
        ),
        factory,
    )


def _get_live_channel_payload(linked_channel) -> dict[str, str] | None:
    cache_key = f"live-channel:{linked_channel.channel_id}"
    cached = live_channel_stream_cache.get(cache_key)
    if cached is not None:
        return cached

    started_at = perf_counter()

    try:
        wrapper = get_wrapper(linked_channel.channel_id)
    except Exception:
        return None

    try:
        resolutions = resolver.resolve_wrapper(
            wrapper,
            resolve_all=False,
            stop_after_first_success=True,
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning(
            "Live channel resolution failed for channel %s: %s",
            linked_channel.channel_id,
            exc,
        )
        return None

    for resolution in resolutions:
        if not resolution.manifests:
            continue
        manifest = resolution.manifests[0]
        payload = {
            "url": manifest.url,
            "referer": manifest.found_at_url,
            "player_type": manifest.player_type.upper(),
        }
        live_channel_stream_cache.set(
            cache_key,
            payload,
            settings.LIVE_CHANNEL_CACHE_TTL_SECONDS,
        )
        _log_timing("live channel resolve", started_at, channel_id=linked_channel.channel_id, success=True)
        return payload

    _log_timing("live channel resolve", started_at, channel_id=linked_channel.channel_id, success=False)
    return None


def _resolve_live_channel_stream(event: LiveEvent, linked_channel, request: Request) -> dict | None:
    payload = _get_live_channel_payload(linked_channel)
    if not payload:
        return None

    return _manifest_to_stream(
        request=request,
        display_name=settings.ADDON_NAME,
        description=f"{linked_channel.name} • {linked_channel.country_label} • {event.title} • {payload['player_type']}",
        manifest_url=payload["url"],
        referer=payload["referer"],
        player_type=payload["player_type"],
    )


def _build_live_streams(event: LiveEvent, request: Request, config: dict[str, str]) -> list[dict]:
    def factory() -> list[dict]:
        started_at = perf_counter()
        streams: list[dict] = []
        seen_urls: set[str] = set()

        started_at = perf_counter()
        attempt_channels = _ordered_live_channels(event, config)[:settings.LIVE_STREAM_MAX_ATTEMPTS]
        if not attempt_channels:
            return streams

        max_workers = max(1, min(settings.LIVE_STREAM_MAX_WORKERS, len(attempt_channels)))
        for offset in range(0, len(attempt_channels), max_workers):
            if perf_counter() - started_at >= settings.LIVE_STREAM_BUDGET_SECONDS:
                _log_timing(
                    "live event budget exhausted",
                    started_at,
                    event_id=event.meta_id,
                    streams=len(streams),
                )
                break

            batch = attempt_channels[offset:offset + max_workers]
            if max_workers == 1:
                linked_channel = batch[0]
                stream = _resolve_live_channel_stream(event, linked_channel, request)
                if not stream:
                    continue
                if stream["url"] in seen_urls:
                    continue

                seen_urls.add(stream["url"])
                streams.append(stream)
                if len(streams) >= _live_stream_result_limit(config):
                    _log_timing("live event stream resolve", started_at, event_id=event.meta_id, streams=len(streams))
                    return streams
                continue

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [
                    (
                        linked_channel,
                        executor.submit(_resolve_live_channel_stream, event, linked_channel, request),
                    )
                    for linked_channel in batch
                ]

                for linked_channel, future in futures:
                    stream = future.result()
                    if not stream:
                        continue
                    if stream["url"] in seen_urls:
                        continue

                    seen_urls.add(stream["url"])
                    streams.append(stream)
                    if len(streams) >= _live_stream_result_limit(config):
                        _log_timing("live event stream resolve", started_at, event_id=event.meta_id, streams=len(streams))
                        return streams

        _log_timing("live event stream resolve", started_at, event_id=event.meta_id, streams=len(streams))
        return streams

    return _remember_streams(
        (
            f"stream:event:{event.meta_id}:{_service_base_url(request)}:"
            f"{_config_fingerprint(config, ('preferredCountryCode', 'liveStreamResults'))}"
        ),
        factory,
    )


def _find_event(meta_id: str) -> LiveEvent | None:
    event = get_schedule_index().get(meta_id)
    if event is not None:
        return event

    refresh_schedule()
    return get_schedule_index().get(meta_id)


@app.on_event("shutdown")
def _shutdown() -> None:
    resolver.close()
    close_pooled_sessions()


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/configure", status_code=307)


@app.get("/configure")
def configure(request: Request) -> HTMLResponse:
    return HTMLResponse(_configure_page_v2(request))


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/manifest.json")
@app.get("/{config}/manifest.json")
def manifest(request: Request, config: str | None = None) -> JSONResponse:
    user_config = _parse_user_config(config)
    return JSONResponse(build_manifest(_service_base_url(request), configured=bool(user_config), user_config=user_config))


@app.head("/manifest.json")
@app.head("/{config}/manifest.json")
def manifest_head(config: str | None = None) -> Response:
    return Response(media_type="application/json")


@app.get("/catalog/tv/{catalog_id}.json")
@app.get("/catalog/tv/{catalog_id}/{extra:path}.json")
@app.get("/{config}/catalog/tv/{catalog_id}.json")
@app.get("/{config}/catalog/tv/{catalog_id}/{extra:path}.json")
def catalog(request: Request, catalog_id: str, extra: str | None = None, config: str | None = None) -> JSONResponse:
    catalog_def = _find_catalog(catalog_id)
    extra_props = _parse_extra(extra)
    user_config = _parse_user_config(config)
    search = extra_props.get("search") or None
    skip = int(extra_props.get("skip", "0") or 0)

    if catalog_def["kind"] == "channels":
        items = filter_channels(
            get_channels(),
            country_code=_effective_country_filter(catalog_def, user_config),
            search=search,
            skip=skip,
        )
        metas = [_channel_preview(channel, request) for channel in items]
    else:
        items = filter_schedule(
            get_schedule(),
            country_code=_effective_country_filter(catalog_def, user_config),
            search=search,
            skip=skip,
            stale_after_minutes=_event_stale_after_minutes(user_config),
        )
        metas = [_event_preview(event, request, user_config) for event in items]

    return JSONResponse({"metas": metas})


@app.get("/meta/tv/{meta_id:path}.json")
@app.get("/{config}/meta/tv/{meta_id:path}.json")
def meta(request: Request, meta_id: str, config: str | None = None) -> JSONResponse:
    kind, value = _parse_meta_id(meta_id)
    user_config = _parse_user_config(config)
    if kind == "channel":
        channel_id = value
        channel_index = get_channel_index()
        channel = channel_index.get(channel_id)
        wrapper = watch_cache.get(f"watch:{channel_id}")
        if channel is None:
            wrapper = wrapper or get_wrapper(channel_id)
            channel = CatalogChannel(
                channel_id=channel_id,
                name=wrapper.channel.name or wrapper.page.title or f"Channel {channel_id}",
                watch_url=f"{settings.BASE_SITE_URL}/watch.php?id={channel_id}",
                search_hint=None,
                group_letter=None,
                country_code="global",
                country_label=settings.COUNTRY_LABELS["global"],
            )

        meta_payload = {
            "id": channel.meta_id,
            "type": "tv",
            "name": channel.name,
            "poster": _poster_url(request, channel.meta_id),
            "posterShape": "poster",
            "background": _poster_url(request, channel.meta_id),
            "description": (
                wrapper.page.description
                if wrapper is not None and wrapper.page.description
                else f"{channel.country_label} channel • ID {channel.channel_id}"
            ),
            "genres": [channel.country_label],
            "videos": [_default_video(channel.meta_id, channel.name)],
            "behaviorHints": {"defaultVideoId": channel.meta_id},
        }
        return JSONResponse({"meta": meta_payload})

    event = _find_event(value)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    event_day_label, event_time_text = _event_display_values(event, user_config)

    meta_payload = {
        "id": event.meta_id,
        "type": "tv",
        "name": event.title,
        "poster": _poster_url(request, event.meta_id),
        "posterShape": "poster",
        "background": _poster_url(request, event.meta_id),
        "description": (
            f"{event_day_label} • {event_time_text} • {event.category}\n"
            f"Channels: {', '.join(channel.name for channel in event.channels)}"
        ),
        "genres": [event.category, *[_country_label(code) for code in event.country_codes]],
        "videos": [_default_video(event.meta_id, event.title)],
        "behaviorHints": {"defaultVideoId": event.meta_id},
    }
    return JSONResponse({"meta": meta_payload})


@app.get("/stream/tv/{meta_id:path}.json")
@app.get("/{config}/stream/tv/{meta_id:path}.json")
def stream(request: Request, meta_id: str, config: str | None = None) -> JSONResponse:
    kind, value = _parse_meta_id(meta_id)
    user_config = _parse_user_config(config)
    if kind == "channel":
        channel = get_cached_channel(value)
        if channel is None:
            wrapper = get_wrapper(value)
            channel = CatalogChannel(
                channel_id=value,
                name=wrapper.channel.name or wrapper.page.title or f"Channel {value}",
                watch_url=f"{settings.BASE_SITE_URL}/watch.php?id={value}",
                search_hint=None,
                group_letter=None,
                country_code="global",
                country_label=settings.COUNTRY_LABELS["global"],
            )
        streams = _build_channel_streams(channel, request, user_config)
        return JSONResponse({"streams": streams})

    event = _find_event(value)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    return JSONResponse({"streams": _build_live_streams(event, request, user_config)})


@app.get("/proxy/{filename:path}")
def proxy_media(
    request: Request,
    filename: str,
    url: str = Query(...),
    referer: str = Query(...),
) -> Response:
    started_at = perf_counter()
    playlist_cache_key = None
    if filename.lower().endswith(".m3u8"):
        playlist_cache_key = _playlist_cache_key(url, referer)
        cached_playlist = playlist_cache.get(playlist_cache_key)
        if cached_playlist is not None:
            _log_timing("proxy playlist cache hit", started_at, filename=filename)
            return Response(content=cached_playlist, media_type="application/vnd.apple.mpegurl")

    session = get_pooled_session()
    headers = {"Referer": referer}

    range_header = request.headers.get("range")
    if range_header:
        headers["Range"] = range_header

    upstream = session.get(
        url,
        timeout=settings.HTTP_TIMEOUT_SECONDS,
        stream=True,
        allow_redirects=True,
        headers=headers,
    )
    upstream.raise_for_status()
    content_type = upstream.headers.get("Content-Type", "application/octet-stream").split(";", 1)[0].strip()

    if playlist_cache_key is not None:
        body = upstream.text
        upstream.close()

        if not _looks_like_hls_playlist(body):
            _log_timing("proxy non-hls playlist passthrough", started_at, filename=filename)
            return Response(content=body, media_type=content_type or "text/plain")

        rewritten = _rewrite_hls_playlist(request, body, upstream.url, referer)
        playlist_cache.set(
            playlist_cache_key,
            rewritten,
            settings.HLS_PLAYLIST_CACHE_TTL_SECONDS,
        )
        _log_timing("proxy playlist rewrite", started_at, filename=filename)
        return Response(content=rewritten, media_type="application/vnd.apple.mpegurl")

    response_headers = {}
    for header_name in ("Accept-Ranges", "Content-Length", "Content-Range"):
        header_value = upstream.headers.get(header_name)
        if header_value:
            response_headers[header_name] = header_value

    if content_type in {"application/javascript", "text/javascript", "text/plain", "text/txt"}:
        content_type = "application/octet-stream"

    return StreamingResponse(
        _stream_upstream(upstream),
        status_code=upstream.status_code,
        media_type=content_type,
        headers=response_headers,
    )


@app.head("/proxy/{filename:path}")
def proxy_media_head(filename: str) -> Response:
    media_type = "application/vnd.apple.mpegurl" if filename.lower().endswith(".m3u8") else "application/octet-stream"
    return Response(media_type=media_type)


@app.get("/assets/logo.svg")
def logo() -> Response:
    svg = render_svg_poster(settings.ADDON_NAME, "Stremio addon", "global")
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/assets/background.svg")
def background() -> Response:
    svg = render_svg_poster(settings.ADDON_NAME, settings.ADDON_DESCRIPTION, "global")
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/assets/poster/{meta_id:path}.svg")
def poster(meta_id: str) -> Response:
    try:
        kind, value = _parse_meta_id(meta_id)
    except HTTPException:
        svg = render_svg_poster(settings.ADDON_NAME, settings.ADDON_DESCRIPTION, "global")
        return Response(content=svg, media_type="image/svg+xml")

    if kind == "channel":
        channel = get_channel_index().get(value)
        if channel is None:
            wrapper = get_wrapper(value)
            title = wrapper.channel.name or wrapper.page.title or f"Channel {value}"
            subtitle = settings.COUNTRY_LABELS["global"]
            accent = "global"
        else:
            title = channel.name
            subtitle = channel.country_label
            accent = channel.country_code
        svg = render_svg_poster(title, subtitle, accent)
        return Response(content=svg, media_type="image/svg+xml")

    event = _find_event(value)
    if event is None:
        svg = render_svg_poster("Live Event", "Unavailable", "global")
        return Response(content=svg, media_type="image/svg+xml")

    accent = event.country_codes[0] if event.country_codes else "global"
    subtitle = f"{event.time_text} • {event.category}"
    svg = render_svg_poster(event.title, subtitle, accent)
    return Response(content=svg, media_type="image/svg+xml")
