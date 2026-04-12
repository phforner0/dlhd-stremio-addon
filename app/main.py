from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import quote, unquote
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from app import settings
from app.cache import TTLCache
from app.http import DEFAULT_HEADERS
from app.manifest import build_manifest
from app.models import CatalogChannel, LiveEvent, WrapperCatalog
from app.posters import render_svg_poster
from app.resolve.player import PlaywrightResolver
from app.scrape.channels import filter_channels, scrape_channels
from app.scrape.schedule import filter_schedule, scrape_schedule
from app.scrape.watch import fetch_wrapper

app = FastAPI(title=settings.ADDON_NAME, docs_url=None, redoc_url=None)
LOGGER = logging.getLogger("dlhd.addon")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "HEAD", "OPTIONS"],
    allow_headers=["*"],
    max_age=86400,
)

channels_cache: TTLCache[list[CatalogChannel]] = TTLCache()
schedule_cache: TTLCache[list[LiveEvent]] = TTLCache()
watch_cache: TTLCache[WrapperCatalog] = TTLCache()
stream_cache: TTLCache[list[dict]] = TTLCache()
live_channel_stream_cache: TTLCache[dict[str, str]] = TTLCache()
resolver = PlaywrightResolver()


def _service_base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


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


def _find_catalog(catalog_id: str) -> dict:
    catalog = settings.CATALOGS_BY_ID.get(catalog_id)
    if not catalog:
        raise HTTPException(status_code=404, detail="unknown catalog")
    return catalog


def get_channels() -> list[CatalogChannel]:
    return channels_cache.remember(
        "channels",
        settings.CHANNELS_CACHE_TTL_SECONDS,
        scrape_channels,
    )


def get_channel_index() -> dict[int, CatalogChannel]:
    return {channel.channel_id: channel for channel in get_channels()}


def refresh_schedule() -> list[LiveEvent]:
    events = scrape_schedule(get_channel_index())
    schedule_cache.set("schedule", events, settings.SCHEDULE_CACHE_TTL_SECONDS)
    return events


def get_schedule() -> list[LiveEvent]:
    return schedule_cache.remember(
        "schedule",
        settings.SCHEDULE_CACHE_TTL_SECONDS,
        lambda: scrape_schedule(get_channel_index()),
    )


def get_wrapper(channel_id: int) -> WrapperCatalog:
    return watch_cache.remember(
        f"watch:{channel_id}",
        settings.WATCH_CACHE_TTL_SECONDS,
        lambda: fetch_wrapper(channel_id),
    )


def _country_label(country_code: str) -> str:
    return settings.COUNTRY_LABELS[country_code]


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


def _event_preview(event: LiveEvent, request: Request) -> dict:
    country_labels = [_country_label(code) for code in event.country_codes]
    return {
        "id": event.meta_id,
        "type": "tv",
        "name": event.title,
        "poster": _poster_url(request, event.meta_id),
        "posterShape": "poster",
        "description": f"{event.day_label} • {event.time_text} • {event.category}",
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
    display_name: str,
    description: str,
    manifest_url: str,
    referer: str,
) -> dict:
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


def _default_video(meta_id: str, title: str) -> dict:
    return {
        "id": meta_id,
        "title": title,
        "released": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "available": True,
    }


def _remember_streams(cache_key: str, factory) -> list[dict]:
    cached = stream_cache.get(cache_key)
    if cached is not None:
        return cached
    streams = factory()
    if not streams:
        streams = factory()
    if not streams:
        return []
    return stream_cache.set(cache_key, streams, settings.STREAM_CACHE_TTL_SECONDS)


def _build_channel_streams(channel: CatalogChannel) -> list[dict]:
    def factory() -> list[dict]:
        try:
            wrapper = get_wrapper(channel.channel_id)
            resolutions = resolver.resolve_wrapper(wrapper, resolve_all=True)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Channel stream resolution failed for %s: %s", channel.channel_id, exc)
            return []

        streams: list[dict] = []
        seen_urls: set[str] = set()
        for resolution in resolutions:
            for manifest in resolution.manifests:
                if manifest.url in seen_urls:
                    continue
                seen_urls.add(manifest.url)
                streams.append(
                    _manifest_to_stream(
                        display_name=settings.ADDON_NAME,
                        description=f"{channel.name} • {channel.country_label} • {resolution.label} • {manifest.player_type.upper()}",
                        manifest_url=manifest.url,
                        referer=manifest.found_at_url,
                    )
                )
        return streams

    return _remember_streams(f"stream:channel:{channel.channel_id}", factory)


def _get_live_channel_payload(linked_channel) -> dict[str, str] | None:
    cache_key = f"live-channel:{linked_channel.channel_id}"
    cached = live_channel_stream_cache.get(cache_key)
    if cached is not None:
        return cached

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
            "Live stream resolution failed for event %s channel %s: %s",
            event.meta_id,
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
        return payload

    return None


def _resolve_live_channel_stream(event: LiveEvent, linked_channel) -> dict | None:
    payload = _get_live_channel_payload(linked_channel)
    if not payload:
        return None

    return _manifest_to_stream(
        display_name=settings.ADDON_NAME,
        description=(
            f"{event.title} • {linked_channel.name} • "
            f"{linked_channel.country_label} • {payload['player_type']}"
        ),
        manifest_url=payload["url"],
        referer=payload["referer"],
    )


def _build_live_streams(event: LiveEvent) -> list[dict]:
    def factory() -> list[dict]:
        streams: list[dict] = []
        seen_urls: set[str] = set()

        attempt_channels = event.channels[:settings.LIVE_STREAM_MAX_ATTEMPTS]
        if not attempt_channels:
            return streams

        max_workers = max(1, min(settings.LIVE_STREAM_MAX_WORKERS, len(attempt_channels)))
        for offset in range(0, len(attempt_channels), max_workers):
            batch = attempt_channels[offset:offset + max_workers]
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [
                    (linked_channel, executor.submit(_resolve_live_channel_stream, event, linked_channel))
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
                    if len(streams) >= settings.LIVE_STREAM_MAX_RESULTS:
                        return streams

        return streams

    return _remember_streams(f"stream:event:{event.meta_id}", factory)


def _find_event(meta_id: str) -> LiveEvent | None:
    for event in get_schedule():
        if event.meta_id == meta_id:
            return event
    for event in refresh_schedule():
        if event.meta_id == meta_id:
            return event
    return None


@app.on_event("shutdown")
def _shutdown() -> None:
    resolver.close()


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/manifest.json")
def manifest(request: Request) -> JSONResponse:
    return JSONResponse(build_manifest(_service_base_url(request)))


@app.head("/manifest.json")
def manifest_head() -> Response:
    return Response(media_type="application/json")


@app.get("/catalog/tv/{catalog_id}.json")
@app.get("/catalog/tv/{catalog_id}/{extra:path}.json")
def catalog(request: Request, catalog_id: str, extra: str | None = None) -> JSONResponse:
    catalog_def = _find_catalog(catalog_id)
    extra_props = _parse_extra(extra)
    search = extra_props.get("search") or None
    skip = int(extra_props.get("skip", "0") or 0)

    if catalog_def["kind"] == "channels":
        items = filter_channels(
            get_channels(),
            country_code=catalog_def["country_code"],
            search=search,
            skip=skip,
        )
        metas = [_channel_preview(channel, request) for channel in items]
    else:
        items = filter_schedule(
            get_schedule(),
            country_code=catalog_def["country_code"],
            search=search,
            skip=skip,
        )
        metas = [_event_preview(event, request) for event in items]

    return JSONResponse({"metas": metas})


@app.get("/meta/tv/{meta_id:path}.json")
def meta(request: Request, meta_id: str) -> JSONResponse:
    kind, value = _parse_meta_id(meta_id)
    if kind == "channel":
        channel_id = value
        channel_index = get_channel_index()
        channel = channel_index.get(channel_id)
        wrapper = get_wrapper(channel_id)
        if channel is None:
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
            "description": wrapper.page.description or f"{channel.country_label} channel",
            "genres": [channel.country_label],
            "videos": [_default_video(channel.meta_id, channel.name)],
            "behaviorHints": {"defaultVideoId": channel.meta_id},
        }
        return JSONResponse({"meta": meta_payload})

    event = _find_event(value)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")

    meta_payload = {
        "id": event.meta_id,
        "type": "tv",
        "name": event.title,
        "poster": _poster_url(request, event.meta_id),
        "posterShape": "poster",
        "background": _poster_url(request, event.meta_id),
        "description": (
            f"{event.day_label} • {event.time_text} • {event.category}\n"
            f"Channels: {', '.join(channel.name for channel in event.channels)}"
        ),
        "genres": [event.category, *[_country_label(code) for code in event.country_codes]],
        "videos": [_default_video(event.meta_id, event.title)],
        "behaviorHints": {"defaultVideoId": event.meta_id},
    }
    return JSONResponse({"meta": meta_payload})


@app.get("/stream/tv/{meta_id:path}.json")
def stream(meta_id: str) -> JSONResponse:
    kind, value = _parse_meta_id(meta_id)
    if kind == "channel":
        channel = get_channel_index().get(value)
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
        streams = _build_channel_streams(channel)
        return JSONResponse({"streams": streams})

    event = _find_event(value)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    return JSONResponse({"streams": _build_live_streams(event)})


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
