# AGENTS.md

## Commands
- Use Python 3.11.
- Dev install for repo work: `pip install -e .[dev]`
- Install browser bits only when touching real stream resolution or smoke checks: `playwright install chromium`
- Run locally: `python -m uvicorn app.main:app --host 0.0.0.0 --port 7000`
- Match CI before handoff: `python -m compileall app` then `pytest`
- If you changed Docker or deploy startup, also run: `docker build -t dlhd-stremio-addon:ci .`
- There is no configured lint/typecheck gate; the actual CI checks are compileall, pytest, and Docker build.

## Structure
- `app/main.py` is the real app entrypoint and contains most route wiring, caching, proxying, stream assembly, and config-token handling.
- `app/manifest.py` defines manifest output and the user-facing config schema.
- `app/settings.py` defines env defaults, fixed catalog inventory, country lists, and the addon version.
- `app/scrape/*` handles upstream HTML scraping; `app/resolve/player.py` is the Playwright-based HLS resolver.

## Gotchas
- Config routes must keep backward compatibility with both legacy raw JSON path segments and newer `cfg-...` base64url tokens; `_parse_user_config()` supports both and tests cover both.
- User config is duplicated in two places: manifest `config` in `app/manifest.py` and the HTML configure UI/parser helpers in `app/main.py`. Keep them in sync when adding or changing options.
- `pytest` is unit-only and does not install Playwright. Real `/stream` resolution is only exercised by smoke checks or manual runs.
- Smoke source of truth is `.github/workflows/smoke.yml`: it installs Playwright, starts a real server on port 7000, and checks `/healthz`, `/manifest.json`, `/catalog/tv/channels_br.json`, `/meta/tv/dlhd:ch:81.json`, and `/stream/tv/dlhd:ch:81.json`.
- Version bumps are multi-file: update `app/settings.py`, `pyproject.toml`, and every example env file that pins `DLHD_ADDON_VERSION` under the repo root and `deploy/*`.
- Catalog/version changes require follow-through in tests and smoke checks. Current assertions hard-code manifest version `0.1.3`, catalog count `28`, and smoke channel `81`.
- `/healthz` is the deploy health endpoint in Docker Compose, Railway, Render, and smoke checks. Keep it stable or update all of those files together.
- Public manifest/logo/poster URLs depend on proxy headers. The Docker CMD intentionally runs `uvicorn` with `--proxy-headers --forwarded-allow-ips='*'`; preserve that behavior when changing startup or deploy config.
