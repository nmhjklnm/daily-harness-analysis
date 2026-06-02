# daily-harness-analysis

A self-hostable pipeline that turns your RSS feeds into a published daily digest.
Every morning it pulls the last 24 hours from a self-hosted feed reader, has an
LLM read each linked source and write a cited summary, renders a card-style
static site, and deploys it. The live reference deployment runs entirely on this
code.

Three independent parts, each in its own directory:

```
 RSS/Atom feeds
      │   backend/   docker compose: Miniflux + RSSHub (+ optional proxy)
      ▼
  Miniflux  ──REST API──►  pipeline/   run.sh, once a day via cron
                               1. fetch last N hours          daily_brief.py
                               2. shallow digest      ◄─LLM─  (codex)
                               3. deep-read each link ◄─LLM─  deepen.sh
                               4. render md → html/pdf        md_to_html.py
                               5. build the card site         site/build*.py
                               ▼
                          site/   static HTML  ──►  deploy.sh (vercel│netlify│rsync│none)
```

Nothing is hardcoded — every secret, path, domain and brand string is read from
`.env` at runtime. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full
data flow and design notes.

## Prerequisites

- **Docker + Docker Compose** — for the feed backend.
- **Python 3.10+** and **[uv](https://docs.astral.sh/uv/)** — `curl -LsSf https://astral.sh/uv/install.sh | sh`. The render stages declare their deps inline (PEP 723); `uv` installs them on demand. PDF rendering uses WeasyPrint, which needs the system Pango/Cairo libs (`apt install libpango-1.0-0 libpangocairo-1.0-0` on Debian/Ubuntu).
- **An agentic LLM CLI** — default is OpenAI's [`codex`](https://github.com/openai/codex) (`npm i -g @openai/codex`). Authenticate with `codex login` (ChatGPT account) **or** set `OPENAI_API_KEY`. Swap the binary with `LLM_CLI=` in `.env`.
- **A static host** (optional) — Vercel, Netlify, or anything that serves a folder. The build output is plain static files.

## Quickstart

```bash
git clone https://github.com/nmhjklnm/daily-harness-analysis
cd daily-harness-analysis

# 1. Bring up the feed backend (Miniflux + RSSHub)
cd backend
cp .env.example .env          # set strong POSTGRES_PASSWORD + admin creds
docker compose up -d
./bootstrap.sh                # waits for Miniflux, mints an API token, imports starter feeds
cd ..

# 2. Configure the pipeline
cp .env.example .env          # paste the MINIFLUX_TOKEN bootstrap printed; set branding + deploy
$EDITOR .env

# 3. Run it once
pipeline/run.sh --shallow-only   # ~3 min: fetch + digest + render (no deep read)
pipeline/run.sh                  # full run: + deep-read each link + card site + deploy

# 4. Preview locally
python3 -m http.server -d site 8000   # → http://localhost:8000
```

To run it every morning, install the cron job:

```bash
pipeline/install.sh           # symlinks run.sh + adds a daily cron entry (07:00 local)
```

## Configuration

All configuration lives in the repo-root `.env` (copy from `.env.example`). The
keys you will actually set:

| Key | What it does |
| --- | --- |
| `MINIFLUX_TOKEN` | API token for your feed reader (printed by `backend/bootstrap.sh`). **Required.** |
| `MINIFLUX_URL` / `MINIFLUX_SSH_HOST` | Where Miniflux runs. Leave SSH host empty when it's local. |
| `LLM_CLI` / `OPENAI_API_KEY` | The LLM engine and its auth. |
| `SITE_BRAND` / `SITE_TAGLINE` | Branding shown on the site. |
| `SITE_DOMAIN` | Public URL, used for canonical links + the live publish check. |
| `SITE_REPO_URL` | The "Source" link in the site's top-right. Point it at your fork. |
| `DEPLOY_TARGET` | `none` · `vercel` · `netlify` · `rsync` · `command`, plus that target's credentials. |

The backend has its own `backend/.env` for the database password and Miniflux
admin credentials — see [backend/README.md](backend/README.md).

## Customizing

- **Feeds** — edit them in the Miniflux web UI (`http://localhost:8080`), or import an OPML. A starter list is in `backend/feeds.opml.example`.
- **Editorial direction** — `pipeline/interests.md` is the guidance the LLM follows when curating. Put the topics you care about there.
- **Look & branding** — set `SITE_BRAND` / `SITE_TAGLINE` / `SITE_REPO_URL`; deeper theme changes live in `site/assets/style.css` and `site/build*.py`.

## License

MIT — see [LICENSE](LICENSE). Bundled Geist fonts are © Vercel under the SIL Open Font License 1.1.
