# Render Deploy Guide

This guide assumes you want to deploy the addon on Render using the included `render.yaml` and `Dockerfile`.

## Before you start

- A Render account
- The addon code pushed to GitHub
- Optional custom domain already controlled by you

If you are deploying from the current workspace instead of a separate standalone repo, set the Render root directory to:

```text
dlhd-stremio-addon
```

## Create the service

You can deploy in either of these ways:

1. Blueprint deploy from `render.yaml`
2. Manual Web Service deploy using the included `Dockerfile`

Recommended: Blueprint.

## Blueprint flow

1. In Render, create a new `Blueprint`.
2. Connect the repository.
3. If needed, point the service root to `dlhd-stremio-addon`.
4. Render should read `render.yaml` and create the web service.

## Environment variables

The included `render.yaml` already defines sane defaults. Override them only if needed.

Most useful knobs:

```text
DLHD_PLAYWRIGHT_MAX_CONCURRENCY=4
DLHD_LIVE_STREAM_MAX_WORKERS=4
DLHD_LIVE_STREAM_MAX_RESULTS=4
DLHD_LIVE_STREAM_MAX_ATTEMPTS=6
DLHD_STREAM_CACHE_TTL=120
DLHD_LIVE_CHANNEL_CACHE_TTL=120
```

Render sets `PORT` automatically.

## Verify deployment

After the service becomes healthy, verify:

```text
https://<your-render-domain>/healthz
https://<your-render-domain>/manifest.json
```

## Add a custom domain

1. Open the service in Render.
2. Go to `Settings` -> `Custom Domains`.
3. Add your domain.
4. Create the DNS record Render requests.
5. Wait for TLS provisioning.

Public install URL:

```text
https://your-domain.example.com/manifest.json
```

## Stremio validation checklist

Validate at least:

- `All Channels`
- `Brazil Streams`
- `United Kingdom Streams`
- `Global / Regional Streams`
- one channel stream
- one small live event stream

## Troubleshooting

- If build fails, make sure Render is using the `Dockerfile` path from this addon.
- If startup is slow, give the service enough warmup time before first smoke tests.
- If large live events are too slow, lower:
  - `DLHD_LIVE_STREAM_MAX_RESULTS`
  - `DLHD_LIVE_STREAM_MAX_ATTEMPTS`
- If the instance is CPU-limited, lower `DLHD_PLAYWRIGHT_MAX_CONCURRENCY`.
