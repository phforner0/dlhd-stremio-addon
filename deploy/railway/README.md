# Railway Deploy Guide

This guide assumes you want to deploy the addon on Railway using the included `Dockerfile` and `railway.json`.

If you want the fastest path with no custom domain, use:

```text
deploy/railway/QUICKSTART_AUTOMATIC_DOMAIN.md
```

## Before you start

- A Railway account
- The addon code pushed to GitHub
- Optional custom domain already controlled by you

If you are deploying from the current workspace instead of a separate standalone repo, set the Railway root directory to:

```text
dlhd-stremio-addon
```

## Create the service

1. Create a new Railway project from your GitHub repository.
2. Select the repository.
3. If needed, set the service root directory to `dlhd-stremio-addon`.
4. Railway should detect `Dockerfile` and `railway.json` automatically.

## Environment variables

Start with these values:

```text
DLHD_BASE_URL=https://dlstreams.top
DLHD_PLAYWRIGHT_MAX_CONCURRENCY=1
DLHD_LIVE_STREAM_MAX_WORKERS=1
DLHD_LIVE_STREAM_MAX_RESULTS=4
DLHD_LIVE_STREAM_MAX_ATTEMPTS=2
DLHD_CHANNEL_STREAM_MAX_ATTEMPTS=3
DLHD_CHANNELS_CACHE_TTL=43200
DLHD_SCHEDULE_CACHE_TTL=120
DLHD_WATCH_CACHE_TTL=1800
DLHD_STREAM_CACHE_TTL=120
DLHD_LIVE_CHANNEL_CACHE_TTL=120
```

You do not need to set `PORT`; Railway injects it automatically.

Production-ready example values are also included in:

```text
deploy/railway/.env.production.example
```

## Verify deployment

After the deploy finishes, verify:

```text
https://<your-railway-domain>/healthz
https://<your-railway-domain>/manifest.json
```

Expected health response:

```json
{"ok":true}
```

## Add a custom domain

1. Open the service in Railway.
2. Go to `Settings` -> `Domains`.
3. Add your domain.
4. Create the DNS record Railway requests.
5. Wait for the certificate to issue.

Public install URL:

```text
https://your-domain.example.com/manifest.json
```

## Stremio validation checklist

After deploy, validate at least these flows:

- `All Channels`
- `Brazil Streams`
- `United States Streams`
- `Global / Regional Streams`
- one channel stream, for example `dlhd:ch:81`
- one small live event stream

## Troubleshooting

- If deploy fails immediately, confirm the root directory is correct.
- If the service starts but URLs are wrong, confirm Railway is forwarding standard proxy headers.
- If `/stream` keeps returning empty arrays, set `DLHD_CHANNEL_STREAM_MAX_ATTEMPTS=3` or higher and restart the service.
- If stream requests are too slow, reduce:
  - `DLHD_LIVE_STREAM_MAX_RESULTS`
  - `DLHD_LIVE_STREAM_MAX_ATTEMPTS`
- If Railway is unstable, keep `DLHD_PLAYWRIGHT_MAX_CONCURRENCY=1`.

Before publishing to users, run through:

```text
deploy/railway/PRODUCTION_CHECKLIST.md
```
