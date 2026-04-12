from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from app import settings
from app.http import DEFAULT_HEADERS, build_session
from app.models import ManifestResult, PlayerResolution, WrapperCatalog

LOGGER = logging.getLogger("dlhd.resolve")


def _log_timing(message: str, started_at: float, **fields: object) -> None:
    elapsed_ms = round((time.perf_counter() - started_at) * 1000)
    details = " ".join(f"{key}={value}" for key, value in fields.items())
    if details:
        LOGGER.debug("%s duration_ms=%s %s", message, elapsed_ms, details)
    else:
        LOGGER.debug("%s duration_ms=%s", message, elapsed_ms)

MANIFEST_RE: tuple[re.Pattern[str], ...] = (
    re.compile(r'(https?://[^\s\'"<>{}\[\]\\]+\.m3u8(?:[?#][^\s\'"<>]*)?)', re.I),
    re.compile(r'(https?://[^\s\'"<>{}\[\]\\]+\.mpd(?:[?#][^\s\'"<>]*)?)', re.I),
    re.compile(r'(https?://[^\s\'"<>{}\[\]\\]+\.(?:mp4|webm|ts|flv)(?:[?#][^\s\'"<>]*)?)', re.I),
)

STREAM_ATTRS: tuple[str, ...] = (
    "src",
    "data-src",
    "data-stream",
    "data-url",
    "data-hls",
    "data-manifest",
    "data-video-url",
    "file",
)

STREAM_JSON_KEYS: tuple[str, ...] = (
    "src",
    "source",
    "file",
    "url",
    "hls",
    "manifest",
    "stream",
)

EMBED_CHANNEL_KEY_RE = re.compile(r"const\s+CHANNEL_KEY\s*=\s*['\"]([^'\"]+)['\"]")
EMBED_SERVERS_RE = re.compile(r"let\s+M3U8_SERVERS\s*=\s*\[(.*?)\]", re.S)
EMBED_PROXY_RE = re.compile(
    r"https?://[^/]+/proxy/(?:top1/cdn|[^/]+)/[^/]+/mono\.css(?:[?#][^\s'\"<>]*)?",
    re.I,
)

JS_PROBES: tuple[str, ...] = (
    "(() => { try { return jwplayer().getPlaylist()[0].file } catch(e){ return null } })()",
    "(() => { try { return jwplayer().getConfig().file } catch(e){ return null } })()",
    "(() => { try { return hls && hls.url } catch(e){ return null } })()",
    "(() => { try { const el = document.querySelector('.video-js'); return el ? videojs(el.id).currentSrc() : null } catch(e){ return null } })()",
    "(() => { try { const el = document.querySelector('video'); return el ? el.src : null } catch(e){ return null } })()",
    "(() => { try { const el = document.querySelector('video source'); return el ? el.src : null } catch(e){ return null } })()",
)

SEGMENT_RE = re.compile(r"/segment[_-]?\d+\.ts", re.I)


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned or None


def _abs(url: str | None, base: str | None) -> str | None:
    from urllib.parse import urljoin

    if not url:
        return None
    return urljoin(base, url.strip()) if base else url.strip()


def _player_type(url: str) -> str:
    lowered = url.lower()
    if ".m3u8" in lowered or _is_embed_proxy_manifest(url):
        return "hls"
    if ".mpd" in lowered:
        return "dash"
    if any(ext in lowered for ext in (".mp4", ".webm", ".ts", ".flv")):
        return "progressive"
    return "unknown"


def _is_embed_proxy_manifest(url: str) -> bool:
    return bool(EMBED_PROXY_RE.search(url))


def _is_manifest(url: str) -> bool:
    return _is_embed_proxy_manifest(url) or any(pattern.search(url) for pattern in MANIFEST_RE)


def _is_playlist(url: str) -> bool:
    if SEGMENT_RE.search(url):
        return False
    if _is_embed_proxy_manifest(url):
        return True
    lowered = url.lower().split("?")[0]
    return lowered.endswith((".m3u8", ".mpd", ".mp4", ".webm", ".flv"))


def _extract_embed_proxy_manifest(html: str, timeout: int = 10) -> list[str]:
    if "CHANNEL_KEY" not in html or "M3U8_SERVERS" not in html or "server_lookup" not in html:
        return []

    channel_match = EMBED_CHANNEL_KEY_RE.search(html)
    servers_match = EMBED_SERVERS_RE.search(html)
    if not channel_match or not servers_match:
        return []

    channel_key = _clean(channel_match.group(1))
    servers = [
        server.strip()
        for server in re.findall(r"['\"]([^'\"]+)['\"]", servers_match.group(1))
        if server.strip()
    ]
    if not channel_key or not servers:
        return []

    try:
        with build_session() as session:
            session.headers.update({"Accept": "application/json, text/plain, */*"})
            selected_server = servers[0]
            for server in servers:
                try:
                    response = session.get(f"https://{server}/status", timeout=timeout)
                    response.raise_for_status()
                    payload = response.json()
                    if isinstance(payload, dict) and payload.get("success") is True:
                        selected_server = server
                        break
                except Exception as exc:  # noqa: BLE001
                    LOGGER.debug("Status probe failed for %s: %s", server, exc)

            response = session.get(
                f"https://{selected_server}/server_lookup",
                params={"channel_id": channel_key},
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
            server_key = _clean(payload.get("server_key")) if isinstance(payload, dict) else None
            if not server_key:
                return []

            proxy_manifest = (
                f"https://{selected_server}/proxy/top1/cdn/{channel_key}/mono.css"
                if server_key == "top1/cdn"
                else f"https://{selected_server}/proxy/{server_key}/{channel_key}/mono.css"
            )

            probe = session.get(proxy_manifest, timeout=timeout)
            probe.raise_for_status()
            if not probe.text.lstrip().startswith("#EXTM3U"):
                return []

            return [proxy_manifest]
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("Bootstrap proxy extraction failed: %s", exc)
        return []


def _extract_embed_proxy_manifest_from_url(url: str, referer: str, timeout: int = 10) -> list[str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return []
    if "embedkclx.sbs" not in parsed.netloc or "/premiumtv/" not in parsed.path:
        return []

    try:
        with build_session() as session:
            session.headers.update({"Referer": referer})
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            return _extract_embed_proxy_manifest(response.text, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("Bootstrap fetch failed for %s: %s", url, exc)
        return []


def _derive_proxy_manifest_from_lookup(response: Any, timeout: int = 10) -> str | None:
    if "server_lookup" not in response.url:
        return None

    channel_id = parse_qs(urlparse(response.url).query).get("channel_id", [None])[0]
    channel_key = _clean(channel_id)
    if not channel_key:
        return None

    try:
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("server_lookup JSON decode failed for %s: %s", response.url, exc)
        return None

    server_key = _clean(payload.get("server_key")) if isinstance(payload, dict) else None
    if not server_key:
        return None

    server_host = urlparse(response.url).netloc
    proxy_manifest = (
        f"https://{server_host}/proxy/top1/cdn/{channel_key}/mono.css"
        if server_key == "top1/cdn"
        else f"https://{server_host}/proxy/{server_key}/{channel_key}/mono.css"
    )

    try:
        with build_session() as session:
            probe = session.get(proxy_manifest, timeout=timeout)
            probe.raise_for_status()
            return proxy_manifest if probe.text.lstrip().startswith("#EXTM3U") else None
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("Proxy manifest validation failed for %s: %s", proxy_manifest, exc)
        return None


def _scan_text(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for pattern in MANIFEST_RE:
        for match in pattern.finditer(text):
            url = match.group(1).rstrip("'\",\\")
            if url not in seen:
                seen.add(url)
                found.append(url)
    return found


def _scan_html_for_manifests(html: str, base_url: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    soup = BeautifulSoup(html, "html.parser")

    for attr in STREAM_ATTRS:
        for element in soup.find_all(attrs={attr: True}):
            url = _abs((element.get(attr) or "").strip(), base_url)
            if url and _is_manifest(url) and url not in seen:
                seen.add(url)
                found.append(url)

    for element in soup.find_all(attrs={"data-setup": True}):
        _extract_json_streams(element.get("data-setup", ""), base_url, found, seen)

    for script in soup.find_all("script"):
        text = script.get_text() or ""
        for block in re.findall(r'\.setup\s*\(\s*(\{.+?\})\s*\)', text, re.S):
            _extract_json_streams(block, base_url, found, seen)

    for url in _scan_text(html):
        if url not in seen:
            seen.add(url)
            found.append(url)

    for url in _extract_embed_proxy_manifest(html):
        if url not in seen:
            seen.add(url)
            found.append(url)

    return found


def _hits(values: Iterable[str], found_at_url: str) -> list[tuple[str, str]]:
    return [(value, found_at_url) for value in values]


def _append_unique_hits(target: list[tuple[str, str]], seen: set[str], values: Iterable[tuple[str, str]]) -> None:
    for url, found_at_url in values:
        if url not in seen:
            seen.add(url)
            target.append((url, found_at_url))


def _has_manifest_hits(*hit_groups: list[tuple[str, str]]) -> bool:
    return any(hit_groups)


def _new_wait_state() -> dict[str, Any]:
    now = time.monotonic()
    return {
        "started_at": now,
        "last_activity_at": now,
        "signal_seen": False,
        "verify_seen": False,
        "lookup_seen": False,
        "manifest_seen": False,
        "frame_seen": False,
    }


def _ordered_player_targets(catalog: WrapperCatalog) -> list[tuple[str, str]]:
    targets: list[tuple[str, str]] = []
    seen_urls: set[str] = set()

    for alternate in catalog.player.alternates:
        if not alternate.active or alternate.url in seen_urls:
            continue
        seen_urls.add(alternate.url)
        targets.append((alternate.label or "alternate", alternate.url))

    if catalog.player.primary.url and catalog.player.primary.url not in seen_urls:
        seen_urls.add(catalog.player.primary.url)
        targets.append((catalog.player.primary.label, catalog.player.primary.url))

    for alternate in catalog.player.alternates:
        if alternate.active or alternate.url in seen_urls:
            continue
        seen_urls.add(alternate.url)
        targets.append((alternate.label or "alternate", alternate.url))

    return targets


def _mark_wait_signal(
    state: dict[str, Any],
    *,
    verify: bool = False,
    lookup: bool = False,
    manifest: bool = False,
    frame: bool = False,
) -> None:
    state["signal_seen"] = True
    state["last_activity_at"] = time.monotonic()
    if verify:
        state["verify_seen"] = True
    if lookup:
        state["lookup_seen"] = True
    if manifest:
        state["manifest_seen"] = True
    if frame:
        state["frame_seen"] = True


def _wait_for_resolution_window(page: Any, state: dict[str, Any], timeout_ms: int) -> None:
    quick_wait_ms = min(3500, timeout_ms)
    quiet_wait_ms = 1000
    poll_ms = 200
    quick_deadline = state["started_at"] + (quick_wait_ms / 1000)
    hard_deadline = state["started_at"] + (timeout_ms / 1000)

    while True:
        now = time.monotonic()
        try:
            if len(page.frames) > 1 and not state["frame_seen"]:
                _mark_wait_signal(state, frame=True)
        except Exception:
            pass

        if state["signal_seen"]:
            if now - state["last_activity_at"] >= (quiet_wait_ms / 1000):
                return
        elif now >= quick_deadline:
            return

        if now >= hard_deadline:
            return

        remaining_ms = max(1, int(min(poll_ms, (hard_deadline - now) * 1000)))
        page.wait_for_timeout(remaining_ms)


def _request_found_at_url(request: Any, fallback_url: str, page_url: str | None = None) -> str:
    try:
        frame = request.frame
    except Exception:  # noqa: BLE001
        frame = None

    frame_url = getattr(frame, "url", None)
    if frame_url and frame_url != "about:blank":
        return frame_url
    if page_url and page_url != "about:blank":
        return page_url
    return fallback_url


def _populate_manifest_results(
    *,
    result: PlayerResolution,
    network_hits: list[tuple[str, str]],
    dom_hits: list[tuple[str, str]],
    js_hits: list[tuple[str, str]],
    iframe_hits: list[tuple[str, str]],
) -> None:
    seen: set[str] = set()

    def add(url: str, source: str, found_at_url: str) -> None:
        if url in seen or not _is_playlist(url):
            return
        seen.add(url)
        result.manifests.append(
            ManifestResult(
                url=url,
                player_type=_player_type(url),
                found_at_url=found_at_url,
                source=source,  # type: ignore[arg-type]
            )
        )

    for url, found_at_url in network_hits:
        add(url, "network", found_at_url)
    for url, found_at_url in dom_hits:
        add(url, "dom", found_at_url)
    for url, found_at_url in js_hits:
        add(url, "js_eval", found_at_url)
    for url, found_at_url in iframe_hits:
        add(url, "iframe_dom", found_at_url)


def _extract_json_streams(raw: str, base: str | None, found: list[str], seen: set[str]) -> None:
    try:
        cfg = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        key_pattern = "|".join(re.escape(key) for key in STREAM_JSON_KEYS)
        loose_pattern = re.compile(
            rf'(?:["\']?(?:{key_pattern})["\']?)\s*:\s*(["\'])(.*?)\1',
            re.I | re.S,
        )
        for match in loose_pattern.finditer(raw):
            url = _abs(match.group(2), base)
            if url and _is_manifest(url) and url not in seen:
                seen.add(url)
                found.append(url)
        return

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key.lower() in STREAM_JSON_KEYS and isinstance(value, str):
                    url = _abs(value, base)
                    if url and _is_manifest(url) and url not in seen:
                        seen.add(url)
                        found.append(url)
                else:
                    walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(cfg)


def _dom_scan(frame_or_page: Any, base_url: str) -> list[str]:
    try:
        html = frame_or_page.content()
        return _scan_html_for_manifests(html, base_url)
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("DOM scan failed on %s: %s", base_url, exc)
        return []


def _js_eval_streams(frame_or_page: Any, base_url: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    for script in JS_PROBES:
        try:
            value = frame_or_page.evaluate(script)
        except Exception:
            continue

        if not value or not isinstance(value, str):
            continue

        url = _abs(value, base_url)
        if url and _is_manifest(url) and url not in seen:
            seen.add(url)
            found.append(url)

    return found


class PlaywrightResolver:
    def __init__(self) -> None:
        self._semaphore = threading.BoundedSemaphore(settings.PLAYWRIGHT_MAX_CONCURRENCY)
        self._thread_state = threading.local()
        self._browser_guard = threading.Lock()
        self._thread_browsers: dict[int, dict[str, Any]] = {}

    def close(self) -> None:
        with self._browser_guard:
            browsers = list(self._thread_browsers.values())
            self._thread_browsers.clear()

        for state in browsers:
            playwright = state.get("playwright")
            browser = state.get("browser")
            try:
                browser.close()
            except Exception:
                pass
            try:
                playwright.stop()
            except Exception:
                pass

    def _drop_thread_browser(self) -> None:
        thread_id = threading.get_ident()
        state = getattr(self._thread_state, "browser_state", None)
        browser = state.get("browser") if state else None
        playwright = state.get("playwright") if state else None

        self._thread_state.browser_state = None

        with self._browser_guard:
            self._thread_browsers.pop(thread_id, None)

        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass

    def _start_thread_browser(self) -> Any:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is not installed. Run `pip install playwright` and `playwright install chromium`."
            ) from exc

        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=True)
        now = time.monotonic()
        state = {
            "playwright": playwright,
            "browser": browser,
            "uses": 0,
            "created_at": now,
            "last_used_at": now,
        }
        self._thread_state.browser_state = state

        with self._browser_guard:
            self._thread_browsers[threading.get_ident()] = state

        return browser

    def _get_thread_browser(self) -> Any:
        state = getattr(self._thread_state, "browser_state", None)
        browser = state.get("browser") if state else None
        if browser is not None:
            try:
                if browser.is_connected():
                    if (
                        settings.PLAYWRIGHT_BROWSER_MAX_USES > 0
                        and state is not None
                        and state["uses"] >= settings.PLAYWRIGHT_BROWSER_MAX_USES
                    ):
                        LOGGER.debug("recycling Playwright browser after max uses=%s", state["uses"])
                        self._drop_thread_browser()
                        return self._start_thread_browser()

                    if (
                        settings.PLAYWRIGHT_BROWSER_MAX_IDLE_SECONDS > 0
                        and state is not None
                        and (time.monotonic() - state["last_used_at"]) >= settings.PLAYWRIGHT_BROWSER_MAX_IDLE_SECONDS
                    ):
                        LOGGER.debug("recycling Playwright browser after idle_seconds=%s", round(time.monotonic() - state["last_used_at"], 2))
                        self._drop_thread_browser()
                        return self._start_thread_browser()

                    return browser
            except Exception:
                pass
            self._drop_thread_browser()

        return self._start_thread_browser()

    def _mark_thread_browser_used(self) -> None:
        state = getattr(self._thread_state, "browser_state", None)
        if state is None:
            return
        state["uses"] += 1
        state["last_used_at"] = time.monotonic()

    def resolve_wrapper(
        self,
        catalog: WrapperCatalog,
        *,
        resolve_all: bool,
        stop_after_first_success: bool = False,
    ) -> list[PlayerResolution]:
        targets = _ordered_player_targets(catalog)

        if not resolve_all and targets:
            targets = targets[:1]

        results: list[PlayerResolution] = []
        for label, player_url in targets:
            result = self.resolve_player(label, player_url)
            results.append(result)
            if stop_after_first_success and result.manifests:
                break
        return results

    def resolve_player(self, label: str, player_url: str) -> PlayerResolution:
        started_at = time.perf_counter()
        result = PlayerResolution(label=label, player_page_url=player_url)
        network_hits: list[tuple[str, str]] = []
        network_seen: set[str] = set()
        iframes_seen: list[str] = []
        iframe_seen_set: set[str] = set()
        inspected_iframe_urls: set[str] = set()

        self._semaphore.acquire()
        try:
            try:
                from playwright.sync_api import TimeoutError as PWTimeout
            except ImportError as exc:
                raise RuntimeError(
                    "Playwright is not installed. Run `pip install playwright` and `playwright install chromium`."
                ) from exc

            transient_playwright = None
            transient_browser = None
            if settings.PLAYWRIGHT_REUSE_BROWSER:
                browser = self._get_thread_browser()
            else:
                from playwright.sync_api import sync_playwright

                transient_playwright = sync_playwright().start()
                browser = transient_playwright.chromium.launch(headless=True)
                transient_browser = browser

            try:
                context = None
                for attempt in range(2 if settings.PLAYWRIGHT_REUSE_BROWSER else 1):
                    try:
                        context = browser.new_context(
                            user_agent=DEFAULT_HEADERS["User-Agent"],
                            locale="pt-BR",
                            ignore_https_errors=True,
                        )
                        break
                    except Exception:
                        if settings.PLAYWRIGHT_REUSE_BROWSER and attempt == 0:
                            self._drop_thread_browser()
                            browser = self._get_thread_browser()
                            continue
                        raise

                if context is None:
                    raise RuntimeError("failed to create browser context")

                page = context.new_page()
                wait_state = _new_wait_state()

                def on_response(response: Any) -> None:
                    url = response.url
                    status = getattr(response, "status", None)
                    if isinstance(status, int) and 200 <= status < 300:
                        if "/verify" in url:
                            _mark_wait_signal(wait_state, verify=True)
                        if "server_lookup" in url:
                            _mark_wait_signal(wait_state, lookup=True)

                        lookup_manifest = _derive_proxy_manifest_from_lookup(response)
                        if lookup_manifest and lookup_manifest not in network_seen:
                            network_seen.add(lookup_manifest)
                            _mark_wait_signal(wait_state, manifest=True)
                            network_hits.append(
                                (lookup_manifest, _request_found_at_url(response.request, player_url, page.url or None))
                            )

                    if not _is_manifest(url) or not _is_playlist(url):
                        return
                    if not isinstance(status, int) or status < 200 or status >= 300:
                        return
                    if url in network_seen:
                        return
                    network_seen.add(url)
                    _mark_wait_signal(wait_state, manifest=True)
                    network_hits.append((url, _request_found_at_url(response.request, player_url, page.url or None)))

                def on_frame_navigated(frame: Any) -> None:
                    src = frame.url
                    if src and src != "about:blank" and src not in iframe_seen_set:
                        iframe_seen_set.add(src)
                        iframes_seen.append(src)
                        _mark_wait_signal(wait_state, frame=True)

                page.on("response", on_response)
                page.on("framenavigated", on_frame_navigated)

                try:
                    page.goto(player_url, timeout=settings.PLAYWRIGHT_TIMEOUT_MS, wait_until="domcontentloaded")
                    result.player_final_url = page.url
                except PWTimeout:
                    result.error = f"timeout while loading {player_url}"
                    return result
                except Exception as exc:  # noqa: BLE001
                    result.error = f"failed to open player: {exc}"
                    return result

                _wait_for_resolution_window(page, wait_state, settings.PLAYWRIGHT_WAIT_SECONDS * 1000)

                page_url = page.url or player_url
                dom_hits = _hits(_dom_scan(page, page_url), page_url)
                js_hits = _hits(_js_eval_streams(page, page_url), page_url)
                iframe_hits: list[tuple[str, str]] = []
                iframe_seen_manifest_set: set[str] = set()

                for frame in page.frames[1:]:
                    frame_url = frame.url
                    if frame_url and frame_url != "about:blank" and frame_url not in iframe_seen_set:
                        iframe_seen_set.add(frame_url)
                        iframes_seen.append(frame_url)
                    if not frame_url or frame_url == "about:blank":
                        continue

                    inspected_iframe_urls.add(frame_url)
                    _append_unique_hits(iframe_hits, iframe_seen_manifest_set, _hits(_dom_scan(frame, frame_url), frame_url))
                    _append_unique_hits(iframe_hits, iframe_seen_manifest_set, _hits(_js_eval_streams(frame, frame_url), frame_url))
                    _append_unique_hits(
                        iframe_hits,
                        iframe_seen_manifest_set,
                        _hits(_extract_embed_proxy_manifest_from_url(frame_url, page_url), frame_url),
                    )

                    if _has_manifest_hits(network_hits, dom_hits, js_hits, iframe_hits):
                        break

                player_host = urlparse(player_url).netloc
                if not _has_manifest_hits(network_hits, dom_hits, js_hits, iframe_hits):
                    external_iframes = [
                        url for url in iframes_seen
                        if url.startswith("http") and urlparse(url).netloc != player_host and url not in inspected_iframe_urls
                    ]

                    for ext_url in external_iframes:
                        ext_net: list[tuple[str, str]] = []
                        ext_net_seen: set[str] = set()
                        ext_page = context.new_page()
                        ext_wait_state = _new_wait_state()

                        def _make_ext_handler(target_hits: list[tuple[str, str]], target_seen: set[str]):
                            def _handler(response: Any) -> None:
                                url = response.url
                                status = getattr(response, "status", None)
                                if isinstance(status, int) and 200 <= status < 300:
                                    if "/verify" in url:
                                        _mark_wait_signal(ext_wait_state, verify=True)
                                    if "server_lookup" in url:
                                        _mark_wait_signal(ext_wait_state, lookup=True)

                                    lookup_manifest = _derive_proxy_manifest_from_lookup(response)
                                    if lookup_manifest and lookup_manifest not in target_seen:
                                        target_seen.add(lookup_manifest)
                                        _mark_wait_signal(ext_wait_state, manifest=True)
                                        target_hits.append(
                                            (lookup_manifest, _request_found_at_url(response.request, ext_url, ext_page.url or None))
                                        )

                                if not _is_manifest(url) or not _is_playlist(url):
                                    return
                                if not isinstance(status, int) or status < 200 or status >= 300:
                                    return
                                if url in target_seen:
                                    return
                                target_seen.add(url)
                                _mark_wait_signal(ext_wait_state, manifest=True)
                                target_hits.append((url, _request_found_at_url(response.request, ext_url, ext_page.url or None)))

                            return _handler

                        def _on_ext_frame_navigated(frame: Any) -> None:
                            src = frame.url
                            if src and src != "about:blank":
                                _mark_wait_signal(ext_wait_state, frame=True)

                        ext_page.on("response", _make_ext_handler(ext_net, ext_net_seen))
                        ext_page.on("framenavigated", _on_ext_frame_navigated)
                        try:
                            ext_page.goto(ext_url, timeout=settings.PLAYWRIGHT_TIMEOUT_MS, wait_until="domcontentloaded")
                            _wait_for_resolution_window(ext_page, ext_wait_state, settings.PLAYWRIGHT_WAIT_SECONDS * 1000)
                            ext_final = ext_page.url or ext_url
                            inspected_iframe_urls.add(ext_url)
                            inspected_iframe_urls.add(ext_final)

                            _append_unique_hits(iframe_hits, iframe_seen_manifest_set, ext_net)
                            _append_unique_hits(iframe_hits, iframe_seen_manifest_set, _hits(_dom_scan(ext_page, ext_final), ext_final))
                            _append_unique_hits(iframe_hits, iframe_seen_manifest_set, _hits(_js_eval_streams(ext_page, ext_final), ext_final))
                            _append_unique_hits(
                                iframe_hits,
                                iframe_seen_manifest_set,
                                _hits(_extract_embed_proxy_manifest_from_url(ext_final, page_url), ext_final),
                            )

                            if not _has_manifest_hits(iframe_hits):
                                for sub_frame in ext_page.frames[1:]:
                                    sub_url = sub_frame.url
                                    if not sub_url or sub_url == "about:blank":
                                        continue
                                    inspected_iframe_urls.add(sub_url)
                                    _append_unique_hits(iframe_hits, iframe_seen_manifest_set, _hits(_dom_scan(sub_frame, sub_url), sub_url))
                                    _append_unique_hits(iframe_hits, iframe_seen_manifest_set, _hits(_js_eval_streams(sub_frame, sub_url), sub_url))
                                    _append_unique_hits(
                                        iframe_hits,
                                        iframe_seen_manifest_set,
                                        _hits(_extract_embed_proxy_manifest_from_url(sub_url, page_url), sub_url),
                                    )

                                    if _has_manifest_hits(iframe_hits):
                                        break
                        except Exception as exc:  # noqa: BLE001
                            LOGGER.debug("External iframe resolution failed for %s: %s", ext_url, exc)
                        finally:
                            ext_page.close()

                        if _has_manifest_hits(iframe_hits):
                            break

                _populate_manifest_results(
                    result=result,
                    network_hits=network_hits,
                    dom_hits=dom_hits,
                    js_hits=js_hits,
                    iframe_hits=iframe_hits,
                )
                result.iframes_seen = iframes_seen
                _log_timing(
                    "resolve player",
                    started_at,
                    label=label,
                    manifests=len(result.manifests),
                    iframes=len(result.iframes_seen),
                    reused_browser=settings.PLAYWRIGHT_REUSE_BROWSER,
                )
                return result
            finally:
                try:
                    context.close()
                except Exception:
                    pass
                if settings.PLAYWRIGHT_REUSE_BROWSER:
                    self._mark_thread_browser_used()
                if transient_browser is not None:
                    try:
                        transient_browser.close()
                    except Exception:
                        pass
                if transient_playwright is not None:
                    try:
                        transient_playwright.stop()
                    except Exception:
                        pass
        finally:
            self._semaphore.release()
