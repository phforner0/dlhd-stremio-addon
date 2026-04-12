# Railway Quickstart With Automatic Domain

Use this flow if you want to publish fast and test the addon with Railway's own public domain.

You do not need a custom domain for this.

## 1. Push the code

Push the addon code to GitHub.

If this addon is still inside the current monorepo, remember that the Railway root directory must be:

```text
dlhd-stremio-addon
```

## 2. Create the Railway project

1. Open Railway.
2. Click `New Project`.
3. Choose `Deploy from GitHub repo`.
4. Select your repository.
5. If Railway asks for the root directory, set:

```text
dlhd-stremio-addon
```

Railway should build from the included `Dockerfile`.

## 3. Add environment variables

Copy these values into the Railway service variables:

```text
DLHD_BASE_URL=https://dlstreams.top
DLHD_ADDON_ID=com.dlhd.stremio
DLHD_ADDON_NAME=DLHD Streams
DLHD_ADDON_VERSION=0.1.3
DLHD_ADDON_DESCRIPTION=Scraped live channels and schedule grouped by country for Stremio.
DLHD_HTTP_TIMEOUT=20
DLHD_PLAYWRIGHT_WAIT=6
DLHD_PLAYWRIGHT_TIMEOUT_MS=20000
DLHD_PLAYWRIGHT_MAX_CONCURRENCY=1
DLHD_LIVE_STREAM_MAX_WORKERS=1
DLHD_LIVE_STREAM_MAX_RESULTS=4
DLHD_LIVE_STREAM_MAX_ATTEMPTS=2
DLHD_CHANNELS_CACHE_TTL=43200
DLHD_SCHEDULE_CACHE_TTL=120
DLHD_WATCH_CACHE_TTL=1800
DLHD_STREAM_CACHE_TTL=120
DLHD_LIVE_CHANNEL_CACHE_TTL=120
DLHD_FAILED_STREAM_CACHE_TTL=30
```

You do not need to define `PORT` manually.

## 4. Wait for the first deploy

After the build finishes, Railway will give you a public URL like:

```text
https://your-service.up.railway.app
```

## 5. Validate the service

Open these URLs in the browser:

```text
https://your-service.up.railway.app/healthz
https://your-service.up.railway.app/manifest.json
```

Expected health response:

```json
{"ok":true}
```

## 6. Install in Stremio

Use this URL in Stremio:

```text
https://your-service.up.railway.app/manifest.json
```

## 7. Smoke test inside Stremio

Test at least:

- `All Channels`
- `Brazil Streams`
- `United States Streams`
- `Global / Regional Streams`
- one channel stream
- one small live event stream

## 8. If it is slow or unstable

Lower these values in Railway and redeploy:

```text
DLHD_PLAYWRIGHT_MAX_CONCURRENCY=1
DLHD_LIVE_STREAM_MAX_WORKERS=1
DLHD_LIVE_STREAM_MAX_ATTEMPTS=2
```
