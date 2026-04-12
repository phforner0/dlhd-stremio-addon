# DLHD Stremio Addon

Standalone Stremio addon service that scrapes the public DLHD site and exposes:

- All Channels
- All Live
- Brazil Streams / Live
- United States Streams / Live
- United Kingdom Streams / Live
- Spain Streams / Live
- France Streams / Live
- Italy Streams / Live
- Portugal Streams / Live
- Turkey Streams / Live
- Poland Streams / Live
- Canada Streams / Live
- Mexico Streams / Live
- Germany Streams / Live
- Global / Regional Streams / Live

## Run

1. Install dependencies:

```bash
pip install -e .
playwright install chromium
```

2. Start the service:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 7000
```

3. Install in Stremio with:

```text
http://localhost:7000/manifest.json
```

## Docker

Build the image:

```bash
docker build -t dlhd-stremio-addon .
```

Run it directly:

```bash
docker run --rm -p 7000:7000 --env-file .env dlhd-stremio-addon
```

Or with Compose:

```bash
cp .env.example .env
docker compose -f docker-compose.example.yml up -d --build
```

Health check:

```text
http://localhost:7000/healthz
```

Manifest URL:

```text
http://localhost:7000/manifest.json
```

## Platform-Specific Guides

- Railway: [`deploy/railway/README.md`](deploy/railway/README.md)
- Railway quickstart with automatic domain: [`deploy/railway/QUICKSTART_AUTOMATIC_DOMAIN.md`](deploy/railway/QUICKSTART_AUTOMATIC_DOMAIN.md)
- Railway production checklist: [`deploy/railway/PRODUCTION_CHECKLIST.md`](deploy/railway/PRODUCTION_CHECKLIST.md)
- Render: [`deploy/render/README.md`](deploy/render/README.md)
- VPS Ubuntu stack: [`deploy/vps/README.md`](deploy/vps/README.md)
- VPS production checklist: [`deploy/vps/PRODUCTION_CHECKLIST.md`](deploy/vps/PRODUCTION_CHECKLIST.md)
- Standalone repo move guide: [`STANDALONE_REPO.md`](STANDALONE_REPO.md)

## GitHub Actions

This repo includes:

- `.github/workflows/ci.yml`
  Runs `compileall`, `pytest`, and a Docker image build on push/PR.
- `.github/workflows/smoke.yml`
  Supports manual local smoke checks and scheduled/manual checks against a deployed addon URL.

Repository variables for `smoke.yml`:

- `ADDON_BASE_URL`
  Public addon base URL, for example `https://dlhd-stremio-addon-production.up.railway.app`
- `SMOKE_CHANNEL_ID`
  Optional channel used for deployed smoke checks. Default: `81`

Repository secrets:

- none required by default

Recommended first setup:

1. add repository variable `ADDON_BASE_URL`
2. optionally add `SMOKE_CHANNEL_ID`
3. run `Smoke` manually once
4. keep the scheduled smoke workflow enabled for production monitoring

## Public HTTPS Deployment

For public Stremio installs, put the addon behind HTTPS.

Example with Caddy on the host machine:

1. Point your domain to the server.
2. Start the addon container on port `7000`.
3. Copy `Caddyfile.example` to your Caddy config and replace `your-domain.example.com`.
4. Reload Caddy.

Your public install URL becomes:

```text
https://your-domain.example.com/manifest.json
```

The container command already enables `--proxy-headers`, so `manifest`, logo, and poster URLs respect HTTPS when the reverse proxy forwards standard headers.

## Railway

Files included:

- `railway.json`
- `Dockerfile`

Deploy flow:

1. Create a new Railway project from this repo.
2. Railway will build from `Dockerfile`.
3. Set any env vars you want from `.env.example`.
4. Wait for the health check on `/healthz`.
5. Attach your custom domain in Railway.

Public manifest URL:

```text
https://your-railway-domain/manifest.json
```

## Render

Files included:

- `render.yaml`
- `Dockerfile`

Deploy flow:

1. Create a new Blueprint or Web Service from this repo.
2. Render will read `render.yaml` and build from `Dockerfile`.
3. Override env vars if needed.
4. Wait for `/healthz` to go green.
5. Add a custom domain if desired.

Public manifest URL:

```text
https://your-render-domain/manifest.json
```

## VPS Ubuntu + Caddy + Docker Compose

Files included:

- `deploy/vps/docker-compose.yml`
- `deploy/vps/Caddyfile`
- `deploy/vps/.env.example`
- `deploy/vps/README.md`

This stack runs both the addon and Caddy in containers and automatically serves HTTPS.

Quick start:

```bash
cd deploy/vps
cp .env.example .env
# edit DOMAIN and ACME_EMAIL
docker compose up -d --build
```

Public manifest URL:

```text
https://your-domain.example.com/manifest.json
```

## Notes

- This version uses only public HTML scraping.
- Channels with ambiguous or regional naming are grouped into `Global / Regional`.
- Stream resolution is done lazily from the `stream` endpoint with Playwright.

## Performance Knobs

The service exposes a few environment variables for tuning live stream resolution:

- `DLHD_PLAYWRIGHT_MAX_CONCURRENCY`
  Limits concurrent Playwright resolutions across requests. Default: `4`
- `DLHD_CHANNEL_STREAM_MAX_RESULTS`
  Stops channel resolution after this many working stream options. Default: `2`
- `DLHD_CHANNEL_STREAM_MAX_ATTEMPTS`
  Caps how many player pages are tried for one channel before returning. Default: `3`
- `DLHD_LIVE_STREAM_MAX_WORKERS`
  Limits parallel channel attempts within one live event request. Default: `4`
- `DLHD_LIVE_STREAM_MAX_RESULTS`
  Stops large live-event resolution after this many working streams. Default: `4`
- `DLHD_LIVE_STREAM_MAX_ATTEMPTS`
  Caps how many linked channels are tried for a live event. Default: `6`
- `DLHD_STREAM_CACHE_TTL`
  Caches fully built `stream` endpoint responses. Default: `120`
- `DLHD_LIVE_CHANNEL_CACHE_TTL`
  Caches the first successful live-channel resolution payload so later live events can reuse it. Default: `120`

The addon now uses two complementary live caches:

- event-level stream cache for repeated requests to the same live meta ID
- per-channel first-success cache so different live events that share a channel can reuse the same resolved HLS source

## Environment Variables

You can override these in `.env` or your deployment platform:

- `PORT`: service listen port inside the container. Default: `7000`
- `DLHD_BASE_URL`: upstream site base URL. Default: `https://dlstreams.top`
- `DLHD_PLAYWRIGHT_MAX_CONCURRENCY`: max simultaneous Playwright resolutions. Default: `4`
- `DLHD_CHANNEL_STREAM_MAX_RESULTS`: max stream options returned for one channel. Default: `2`
- `DLHD_CHANNEL_STREAM_MAX_ATTEMPTS`: max player pages attempted for one channel. Default: `3`
- `DLHD_LIVE_STREAM_MAX_WORKERS`: max parallel linked-channel attempts inside one live request. Default: `4`
- `DLHD_LIVE_STREAM_MAX_RESULTS`: max stream options returned for a large live event. Default: `4`
- `DLHD_LIVE_STREAM_MAX_ATTEMPTS`: max linked channels attempted for a live event. Default: `6`
- `DLHD_CHANNELS_CACHE_TTL`: channel catalog cache TTL. Default: `43200`
- `DLHD_SCHEDULE_CACHE_TTL`: live schedule cache TTL. Default: `120`
- `DLHD_WATCH_CACHE_TTL`: `watch.php` metadata cache TTL. Default: `1800`
- `DLHD_STREAM_CACHE_TTL`: event/channel stream response cache TTL. Default: `120`
- `DLHD_LIVE_CHANNEL_CACHE_TTL`: per-channel live resolution cache TTL. Default: `120`
- `DLHD_FAILED_STREAM_CACHE_TTL`: currently used for local tuning only. Default: `30`

Production-oriented examples included:

- root: [`.env.production.example`](.env.production.example)
- Railway: [`deploy/railway/.env.example`](deploy/railway/.env.example)
- Railway production: [`deploy/railway/.env.production.example`](deploy/railway/.env.production.example)
- Render: [`deploy/render/.env.example`](deploy/render/.env.example)
- VPS: [`deploy/vps/.env.production.example`](deploy/vps/.env.production.example)
