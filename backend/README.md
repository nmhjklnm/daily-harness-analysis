# Feed Backend

Miniflux v2 + RSSHub + Redis, self-hostable with a single compose file.
After setup the pipeline uses a Miniflux API token — no passwords in the pipeline.

## Prerequisites

- Docker Engine ≥ 24 with the Compose plugin (`docker compose version`)
- `curl`, `python3` (for bootstrap.sh)

## Quick start

```bash
# 1. Copy and edit the env file
cp .env.example .env
$EDITOR .env          # set POSTGRES_PASSWORD, MINIFLUX_ADMIN_PASSWORD

# 2. Start the stack
docker compose up -d

# 3. Bootstrap: wait for readiness, mint token, import starter feeds
bash bootstrap.sh
```

`bootstrap.sh` prints a `MINIFLUX_TOKEN=...` line. Copy that value into the
repo-root `.env` (not this directory's `.env`):

```
MINIFLUX_TOKEN=<value printed by bootstrap.sh>
```

## Web UI

Open `http://localhost:8080` and log in with the credentials from `.env`.
Default port is bound to `127.0.0.1` — change the `ports:` entry in
`docker-compose.yml` if you need external access.

## Adding feeds

- **Web UI**: Settings → Feeds → Add feed.
- **API**: `POST /v1/feeds` with `Authorization: X-Auth-Token <token>`.
- **Bulk**: edit `feeds.opml.example` and re-run the import endpoint:
  ```bash
  curl -u "$MINIFLUX_ADMIN_USERNAME:$MINIFLUX_ADMIN_PASSWORD" \
       -X POST -F "file=@feeds.opml.example" \
       http://localhost:8080/v1/import
  ```

## RSSHub internal URLs

Feeds using `http://rsshub:1200/...` (Hugging Face Daily Papers, GitHub
Trending) are fetched over the internal docker network. They must have
**fetch_via_proxy = OFF** (the default). Setting the proxy flag on these
feeds will break them because the proxy only handles external traffic.

## Optional firewall proxy (gost)

For sites blocked in your region, gost bridges an upstream SOCKS5 proxy to
an HTTP proxy on port 8118.

```bash
# 1. Uncomment and set UPSTREAM_SOCKS5 in .env
# 2. Start with the proxy profile
docker compose --profile proxy up -d

# 3. In Miniflux, add blocked feeds with "Fetch via proxy" enabled.
```

Only feeds explicitly marked with "Fetch via proxy" use gost; other feeds
are unaffected. `docker compose up -d` (without `--profile proxy`) does NOT
start gost.

## Stopping / removing

```bash
docker compose down          # stop containers, keep volumes
docker compose down -v       # stop containers AND delete volumes (data loss)
```
