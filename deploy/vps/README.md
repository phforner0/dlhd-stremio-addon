# VPS Ubuntu Deploy

This stack runs the addon behind HTTPS with `Docker Compose + Caddy`.

## Server prep

On Ubuntu:

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-plugin
sudo systemctl enable --now docker
```

Optional firewall:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

## Configure

```bash
cd deploy/vps
cp .env.example .env
```

Edit `.env` and set:

- `DOMAIN`
- `ACME_EMAIL`

## Start

```bash
docker compose up -d --build
```

## Check

```bash
docker compose ps
docker compose logs -f addon
docker compose logs -f caddy
```

Public manifest URL:

```text
https://your-domain.example.com/manifest.json
```
