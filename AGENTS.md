# AGENTS.md

## Commands
- Use Python 3.11 (`requires-python >=3.11`; CI pins 3.11).
- Repo/dev install: `pip install -e .[dev]`
- Install Chromium only when exercising real stream resolution or smoke checks: `playwright install chromium`
- Run locally: `python -m uvicorn app.main:app --host 0.0.0.0 --port 7000`
- Match CI before handoff: `python -m compileall app` then `pytest`
- If you changed Docker or deploy startup, also run: `docker build -t dlhd-stremio-addon:ci .`
- There is no lint/typecheck/pre-commit gate; CI is `compileall`, `pytest`, and a separate Docker build.

## Focused Checks
- Config/catalog/route changes: `pytest tests/test_user_config.py tests/test_catalogs.py tests/test_routes.py tests/test_main_helpers.py`
- Proxy/security/HLS rewrite changes: `pytest tests/test_proxy.py tests/test_settings_proxy_hosts.py tests/test_logging.py`
- Resolver/bootstrap policy changes: `pytest tests/test_resolve_policy.py tests/test_resolve_fixtures.py`
- Cache/HTTP/upstream health changes: `pytest tests/test_cache.py tests/test_http.py tests/test_upstream_health.py`
- Artwork/Stremio UI changes: `pytest tests/test_artwork.py tests/test_stremio_ui.py`
- Country/schedule classification changes: `pytest tests/test_country.py tests/test_schedule.py tests/test_catalogs.py`

## Structure
- `app/main.py` is the real entrypoint: route wiring, configure page HTML/JS, cache orchestration, proxy rewrite, stream assembly, and config-token parsing all live here.
- `app/user_config.py` is the source of truth for manifest config fields and configure UI select fields.
- `app/manifest.py` builds the manifest and the user-focused catalog subset.
- `app/settings.py` is the source of truth for addon version, env defaults, country lists, and generated fixed catalogs.
- `app/scrape/*` scrapes DLHD HTML; `app/resolve/player.py` resolves player pages/HLS with Playwright and HTTP bootstrap fallbacks.
- `app/artwork.py` and `app/posters.py` feed dynamic remote artwork and SVG fallbacks for `/assets/...`.

## Gotchas
- Configured route prefixes accept both legacy raw JSON and `cfg-...` base64url tokens for `/manifest`, `/catalog`, `/meta`, and `/stream`; preserve compatibility through `app.user_config.parse_user_config`.
- Change user-config fields in `app/user_config.py`; `app/main.py` only renders/encodes the configure page via `configure_select_fields()`.
- Catalog inventory is generated from `VISIBLE_COUNTRY_CODES` in `app/settings.py`; tests and local smoke currently expect 28 catalogs.
- `pytest` is unit-only and does not install Chromium. Real `/stream` behavior is only covered by smoke/manual runs.
- `.github/workflows/smoke.yml` is the smoke source of truth: local smoke installs Chromium, starts the app on port 7000, asserts manifest version `0.1.3` and 28 catalogs, then probes the first 20 `channels_br` items until one returns streams.
- `/proxy` is security-sensitive: built-in hosts come from `app/resolve/providers.py`, env hosts come from `DLHD_PROXY_ALLOWED_HOSTS`, private IPs are blocked, redirect targets are revalidated, and dynamic manifest hosts require trusted player sources.
- Logs intentionally redact raw proxy URLs/referers/config tokens; keep `tests/test_logging.py` green when touching logging.
- Version bumps are multi-file: update `app/settings.py`, `pyproject.toml`, every `.env*.example` that pins `DLHD_ADDON_VERSION`, and hardcoded version checks in smoke/tests.
- `/healthz` is the deploy health endpoint in Docker Compose, Railway, Render, and smoke checks. Keep it stable or update all of those files together.
- Public manifest/logo/poster URLs depend on proxy headers. Preserve the Docker `uvicorn` flags `--proxy-headers --forwarded-allow-ips='*'` when changing startup or deploy config.
