# Architecture

## The three parts

| Dir | Role | Lifecycle |
| --- | --- | --- |
| `backend/` | Self-hosted feed reader (Miniflux + RSSHub, optional proxy) | Long-running Docker stack |
| `pipeline/` | Daily orchestration: fetch → digest → deep-read → render → deploy | Runs once a day (cron) |
| `site/` | Static card-site builder + assets (CSS, JS, fonts) | Invoked by the pipeline; output is plain HTML |

The parts are decoupled: the pipeline only talks to the backend over the
Miniflux REST API, and only talks to the site by writing HTML files into it.
You can replace any one of them without touching the others.

## Daily data flow (`pipeline/run.sh`)

Each run writes a layered, inspectable artifact tree under `OUTPUT_DIR/<date>/`:

```
01-raw.json        raw entries from Miniflux for the window      (daily_brief.py)
03-digest.md       shallow one-line-per-item digest  ◄─ LLM      (daily_brief.py → codex)
03-digest.{html,pdf}  rendered shallow digest                    (md_to_html.py)
04-sources/        per-link content the LLM fetched + condensed  (deepen.sh)
05-deep.md         deep digest, each item cited [^N]  ◄─ LLM     (deepen.sh → codex, agentic)
06-deep.{html,pdf} rendered deep digest                          (md_to_html.py)
media.json         per-card thumbnail manifest                   (enrich.py / enrich_gate.sh)
<date>.html        the published card-site issue page            (site/build.py)
```

After the issue page is built, `render_site.sh` regenerates the whole site from
**all** issue pages: the landing (`index.html`, newest issue as hero + archive),
the browse-all page (`archive.html`), the how-it's-made page (`how.html`), and a
client-side search index. Then `deploy.sh` publishes `site/`, and
`verify_publish.sh` polls the live URL to confirm the issue is actually up,
re-deploying up to `PUBLISH_ATTEMPTS` times before alerting via `notify_fail.sh`.

## Why fetch on ingestion time, not publish time

`daily_brief.py` filters entries by **when Miniflux first stored them**
(`created_at`), not by the feed's self-reported `published_at`. Several sources
(e.g. RSSHub's Hugging Face daily-papers, some newsletters) stamp `pubDate` days
in the past, so a naive `published_after` window silently drops papers that only
just arrived. It pre-filters server-side with `changed_after` and keeps entries
genuinely created within the window — which also stops an old item from
re-surfacing just because its content (an HN score, say) changed.

## Design principles

- **Nothing hardcoded.** Every secret, path, domain and brand string comes from
  `.env`. `pipeline/config.sh` (shell) and `dha_config.py` (Python) load it and
  derive defaults relative to the repo root. Env vars already set in the
  environment always win over `.env`, so CI / one-off overrides work.
- **The publish layer is additive and non-fatal.** The proven `md`/`html`/`pdf`
  digest is produced and verified first; the card-site render and deploy run
  afterward and can never break or alter it. `deploy.sh` always exits 0.
- **Issue titles are cached per day** (`<date>/.issue-title`) so re-renders are
  byte-stable even though title derivation goes through a non-deterministic LLM.
- **The LLM is pluggable.** Stages shell out to `$LLM_CLI` (default `codex`).
  The deep-read stage relies on the CLI's built-in web fetch/search; a backend
  without that capability would need the deepen stage adapted.

## Backend notes

Miniflux reaches RSSHub over the internal Docker network
(`http://rsshub:1200/...`); those feeds must be added with
`fetch_via_proxy=false`. Miniflux needs `FETCHER_ALLOW_PRIVATE_NETWORKS=1` to
reach that private address at all. The optional `gost` proxy (compose profile
`proxy`) bridges an upstream SOCKS5 to an HTTP proxy for users behind a
firewall; it's off by default and only used by feeds explicitly added with
`fetch_via_proxy=true`. Details in [../backend/README.md](../backend/README.md).
