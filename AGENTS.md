# AGENTS.md

## Commands
- Use Python 3.11.
- Repo/dev install: `pip install -e .[dev]`
- Install Chromium only when exercising real stream resolution or smoke checks: `playwright install chromium`
- Run locally: `python -m uvicorn app.main:app --host 0.0.0.0 --port 7000`
- Match CI before handoff: `python -m compileall app` then `pytest`
- If you changed Docker or deploy startup, also run: `docker build -t dlhd-stremio-addon:ci .`
- There is no lint/typecheck gate; CI is `compileall`, `pytest`, and a separate Docker build.

## Focused Checks
- Config/catalog changes: `pytest tests/test_main_helpers.py tests/test_catalogs.py`
- Proxy changes: `pytest tests/test_proxy.py`
- Cache / HTTP / resolver policy changes: `pytest tests/test_cache.py tests/test_http.py tests/test_resolve_policy.py`

## Structure
- `app/main.py` is the real entrypoint: route wiring, configure page HTML/JS, cache orchestration, proxy rewrite, stream assembly, and config-token parsing all live here.
- `app/manifest.py` builds the manifest and the configurable catalog subset.
- `app/settings.py` is the source of truth for addon version, env defaults, country lists, and generated fixed catalogs.
- `app/scrape/*` handles upstream HTML scraping; `app/resolve/player.py` is the Playwright-based HLS resolver.

## Gotchas
- Configured routes accept both legacy raw JSON path segments and newer `cfg-...` base64url tokens; keep `_parse_user_config()` compatibility for `/manifest`, `/catalog`, `/meta`, and `/stream`.
- User-config options are duplicated between manifest `config` in `app/manifest.py` and the configure UI fields/JS in `app/main.py`; change both together.
- Catalog inventory is generated from `VISIBLE_COUNTRY_CODES` in `app/settings.py`; tests and local smoke currently expect 28 catalogs.
- `pytest` is unit-only and does not install Chromium. Real `/stream` behavior is only covered by smoke/manual runs.
- `.github/workflows/smoke.yml` is the smoke source of truth: local smoke installs Chromium, starts the app on port 7000, asserts manifest version `0.1.3` and 28 catalogs, then probes the first 20 `channels_br` items until one returns streams.
- `/proxy` is security-sensitive: allowed hosts come from `DLHD_PROXY_ALLOWED_HOSTS`, private IPs are blocked, and redirect targets are revalidated. Keep `tests/test_proxy.py` green when changing it.
- Version bumps are multi-file: update `app/settings.py`, `pyproject.toml`, and every `.env*.example` that pins `DLHD_ADDON_VERSION` under the repo root and `deploy/*`.
- `/healthz` is the deploy health endpoint in Docker Compose, Railway, Render, and smoke checks. Keep it stable or update all of those files together.
- Public manifest/logo/poster URLs depend on proxy headers. Preserve the Docker `uvicorn` flags `--proxy-headers --forwarded-allow-ips='*'` when changing startup or deploy config.
