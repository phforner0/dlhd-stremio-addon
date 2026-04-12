# VPS Production Checklist

Use this checklist before declaring the VPS deployment ready.

## DNS

- `A` or `AAAA` record points the domain to the VPS
- DNS is already propagated enough for certificate issuance
- Final URL decided

Example:

```text
https://your-domain.example.com/manifest.json
```

## Server baseline

- Ubuntu updated
- Docker installed
- Docker Compose plugin installed
- Docker enabled on boot
- Firewall open for `22`, `80`, `443`
- SSH access verified

Recommended:

- non-root deploy user
- SSH keys only
- password login disabled
- `fail2ban` enabled

## Addon config

- `deploy/vps/.env` created from `.env.example`
- `DOMAIN` set correctly
- `ACME_EMAIL` set correctly
- concurrency limits reviewed for server size

Suggested starting point on a small VPS:

```text
DLHD_PLAYWRIGHT_MAX_CONCURRENCY=2
DLHD_LIVE_STREAM_MAX_WORKERS=2
DLHD_LIVE_STREAM_MAX_RESULTS=4
DLHD_LIVE_STREAM_MAX_ATTEMPTS=4
```

## Compose stack

- `docker compose up -d --build` completed successfully
- `addon` container healthy
- `caddy` container healthy/running
- `docker compose ps` looks correct

## HTTPS

- `https://your-domain.example.com/healthz` works
- `https://your-domain.example.com/manifest.json` works
- certificate issued successfully
- no mixed-content or redirect-loop issues

## Stremio validation

- addon installs from the public `manifest.json`
- `All Channels` loads
- `Brazil Streams` loads
- `United States Streams` loads
- `United Kingdom Streams` loads
- `Global / Regional Streams` loads
- one channel stream plays
- one small live event stream plays

## Observability

- `docker compose logs -f addon` reviewed
- `docker compose logs -f caddy` reviewed
- health endpoint checked manually
- restart behavior checked with `docker restart`

## Performance review

- first channel stream latency acceptable
- repeat channel requests faster because of cache
- repeat live requests faster because of event and per-channel cache
- large live events do not overload the VPS

If the server struggles, reduce:

- `DLHD_PLAYWRIGHT_MAX_CONCURRENCY`
- `DLHD_LIVE_STREAM_MAX_WORKERS`
- `DLHD_LIVE_STREAM_MAX_ATTEMPTS`

## Maintenance

- documented deploy command
- documented rollback path
- update routine defined
- domain renewal ownership clear
- backup/restore expectations clear

## Suggested update flow

```bash
git pull
cd deploy/vps
docker compose up -d --build
docker compose logs -f addon
```
