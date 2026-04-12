# Railway Production Checklist

Use this checklist before publishing the Railway URL to users.

## Repository / root directory

- addon code is in its own repository, or
- Railway root directory is set to `dlhd-stremio-addon`
- Railway detected `Dockerfile`
- Railway detected `railway.json`

## Environment

- production values copied from `deploy/railway/.env.production.example`
- `DLHD_PLAYWRIGHT_MAX_CONCURRENCY` reviewed
- `DLHD_LIVE_STREAM_MAX_WORKERS` reviewed
- `DLHD_LIVE_STREAM_MAX_ATTEMPTS` reviewed

Safe starting point for Railway:

```text
DLHD_PLAYWRIGHT_MAX_CONCURRENCY=2
DLHD_LIVE_STREAM_MAX_WORKERS=2
DLHD_LIVE_STREAM_MAX_RESULTS=4
DLHD_LIVE_STREAM_MAX_ATTEMPTS=4
```

## Health

- deployment finished successfully
- `/healthz` returns `{"ok":true}`
- `/manifest.json` returns the addon manifest

## Stremio validation

- addon installs from Railway public URL
- `All Channels` loads
- `Brazil Streams` loads
- `United States Streams` loads
- `Global / Regional Streams` loads
- one channel stream works
- one small live event stream works

## Domain

- Railway domain works, or
- custom domain is attached and certificate issued

Final install URL:

```text
https://your-domain.example.com/manifest.json
```

## Performance / stability

- first stream requests complete in acceptable time
- repeated live requests get faster because of cache
- no repeated deploy crashes during live scraping

If Railway is unstable, lower:

- `DLHD_PLAYWRIGHT_MAX_CONCURRENCY`
- `DLHD_LIVE_STREAM_MAX_WORKERS`
- `DLHD_LIVE_STREAM_MAX_ATTEMPTS`

## Logs

- build logs clean
- startup logs clean
- no repeated `500` on `stream` requests during smoke testing
