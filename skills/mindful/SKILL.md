---
name: mindful
description: "Proactively read the latest Daily Harness Analysis digest and connect the few ideas relevant to what the user is working on now to the task at hand, each with a ready way to try it."
license: Apache-2.0
metadata:
  author: nmhjklnm
  version: "1.0"
---

# mindful

Read your Daily Harness Analysis digest *for* the user and **proactively apply
it to what they're doing**. The point is not to print the news — it's to notice
the one or two ideas, tools, or papers in the latest issues that could improve
the task at hand, and say concretely how to use them.

> **Set your site URL first.** This skill talks to *your* deployed digest. Replace
> `$SITE` below with the base URL you configured as `SITE_DOMAIN` in the repo
> `.env` (e.g. `https://digest.example.com`, or `http://localhost:8000` when
> previewing locally). Everything below assumes `$SITE` resolves to it.

## Be proactive — this is the whole job

Don't wait to be told what to look for, and don't dump a list:

1. **Figure out the current work first.** Before fetching anything, form a 1–2
   line "current focus" from the live context: the cwd, recent/open files, the
   git diff, what the user has been asking in this session. (If there is
   genuinely no signal, fall back to a plain digest of the latest issue — see
   "代读 mode" below — rather than asking.)
2. **Connect, don't catalog.** For each idea you surface, lead with *why it's
   relevant to their work* and *how they'd apply it here* — then the action.
   "You're building a memory store; DeltaMem's residual-tree dedup is the same
   problem — `git clone …`" beats "Here are today's memory papers."
3. **Be selective.** 2–5 items, best first. If nothing is strongly relevant, say
   so in one line and surface the single most interesting thing instead of
   padding.

## How to fetch (progressive disclosure — don't over-pull)

The site is static; *you* are the search.

1. `GET $SITE/ideas/index.json` — a small **manifest**: `latest`, all `dates`,
   `topics` (id + label + count), `kinds`, and a `date_url` template. No item
   bodies; it just tells you what exists.
2. `GET` the date slice(s) you need via `date_url`, **default `latest`**:
   `$SITE/ideas/date/<date>.json` → that issue's full items, each `{title, topic,
   topic_label, kind, summary, source_url, use, page_url}`. Widen to a date range
   **only** when the user asks for history or a keyword/topic needs it (fetch a
   few days, not everything).
3. **Filter locally** — default date = `latest`, default topic = all. Match the
   user's focus / args against `title` + `summary` + `topic` + `kind`.

## Args (compose freely)

- `/mindful` — latest issue, all topics, ranked by relevance to current work.
- `/mindful <keyword>` — keyword filter (latest by default; fetch more dates for
  history).
- `/mindful <topic>` — map the word to a `topics[].id`/`label` from the manifest,
  filter to it.
- `/mindful <date | range>` — fetch those date slice(s).
- mix them: `/mindful memory this week`, `/mindful safety repo`.

## Output

- One-line lead: e.g. `3 items today relate to your <X>:` (or, if weak, `nothing
  strongly relevant, but this one's worth a look:`).
- Per item, tight: **title** — one line on why it fits *their* work · `use`
  (the ready command, e.g. `git clone …` / `read <url>`) · `source_url`.
- That's it. This output replaces the user reading the site themselves.

## 代读 mode (no clear current task)

If there's no usable signal about what they're working on, just proactively
digest the **latest** issue: the 3–5 most notable items with a one-line take and
each item's `use`. Don't ask "what are you working on?" — read it for them.

## Rules

- **Read-only.** Surface the `use` command; never auto-`git clone`/install.
- Don't fetch every date slice "to be safe" — manifest first, then the minimum
  dates. Default is just `latest`.
- Always include each item's real `source_url` so the user can go deeper.
