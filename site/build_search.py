#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Site-wide search index generator for the daily digest.

Scans every ``<YYYY-MM-DD>.html`` issue page in the gallery dir and extracts
each content item into a compact record for client-side search:

    {"title", "desc", "section", "date", "url": "<date>.html#item-N"}

Emits ``assets/search-index.json`` (a JSON array). The index is intentionally
small: HTML is stripped, descriptions are truncated to ~140 chars. The site's
``app.js`` lazy-fetches this on first focus of the masthead ``#site-search``
input and does plain substring/token matching over it.

Design contract (mirrors build.py / build_index.py):
  - PURE PARSE of the BUILT issue HTML (no source markdown dependency), so the
    index always reflects what is actually served.
  - IDEMPOTENT: same inputs -> byte-identical output (items sorted by date DESC
    then item number ASC; atomic write via os.replace).
  - NON-FATAL by contract: callers (render_site.sh) run this in a non-fatal
    context. One unparseable page is skipped (logged to stderr), never aborts.
  - No external deps (stdlib only): regex extraction is sufficient for this
    generator's own controlled output markup.

Usage:
    python3 build_search.py                       # uses the dir this file lives in
    python3 build_search.py --dir <gallery> --out <search-index.json>
"""

from __future__ import annotations

import argparse
import html as _html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

ISSUE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.html$")

# Short description budget (chars). Keeps the shipped JSON small; the dropdown
# only shows a one-line snippet anyway.
DESC_MAX = 140

# Section slug -> human label (zh · en), matching the issue section heads. Used
# so a search result can show which category it came from without re-parsing.
SECTION_LABELS = {
    "papers": "论文 · Papers",
    "projects": "开源 / 项目 · Projects",
    "industry": "行业动态 · Industry News",
    "blog": "博客文章 · Blog Posts",
    "trending": "GitHub 热门 · GitHub Trending",
}

# Each content section block as emitted by build.py:
#   <section class="section reveal" id="<slug>" ...> ... </section>
# We grab the slug + the section's inner HTML, then walk its <article id="item-N">.
SECTION_BLOCK_RE = re.compile(
    r'<section class="section[^"]*"\s+id="(?P<slug>[^"]+)"[^>]*>(?P<body>.*?)</section>',
    re.S,
)

# A single footnoted content item (compact OR card). build.py always gives a
# footnoted item a stable id="item-N"; non-footnoted compact rows have no id and
# are intentionally skipped (they have no per-item anchor to link to).
ITEM_RE = re.compile(
    r'<article class="item[^"]*"\s+id="item-(?P<num>\d+)">(?P<body>.*?)</article>',
    re.S,
)

# Within an item: the title text is the FIRST <a> inside <h3 class="item__title">
# (the source link). The body text is inside <p class="item__body">.
TITLE_RE = re.compile(
    r'<h3 class="item__title">.*?<a [^>]*>(?P<title>.*?)</a>', re.S
)
BODY_RE = re.compile(r'<p class="item__body">(?P<body>.*?)</p>', re.S)

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def strip_html(s: str) -> str:
    """Drop tags, unescape entities, collapse whitespace. Never raises."""
    if not s:
        return ""
    s = TAG_RE.sub("", s)
    s = _html.unescape(s)
    s = WS_RE.sub(" ", s)
    return s.strip()


def truncate(s: str, n: int = DESC_MAX) -> str:
    """Truncate on a char budget with an ellipsis, trimming a dangling space."""
    if len(s) <= n:
        return s
    return s[:n].rstrip() + "…"  # …


def extract_items(html_text: str, iso: str) -> list:
    """Return the list of item records for one issue's HTML.

    Only content sections (papers/projects/industry/blog/trending) are scanned;
    the highlights/lead block and the references list are deliberately excluded
    (highlights are duplicates of body items, refs are bare citations).
    """
    items = []
    for sm in SECTION_BLOCK_RE.finditer(html_text):
        slug = sm.group("slug")
        if slug not in SECTION_LABELS:
            continue
        sec_label = SECTION_LABELS[slug]
        body = sm.group("body")
        for im in ITEM_RE.finditer(body):
            num = im.group("num")
            ibody = im.group("body")
            tm = TITLE_RE.search(ibody)
            title = strip_html(tm.group("title")) if tm else ""
            if not title:
                # No linked title -> nothing meaningful to surface; skip.
                continue
            bm = BODY_RE.search(ibody)
            desc = truncate(strip_html(bm.group("body"))) if bm else ""
            items.append(
                {
                    "title": title,
                    "desc": desc,
                    "section": sec_label,
                    "date": iso,
                    "url": f"{iso}.html#item-{num}",
                }
            )
    return items


def build_index(gal_dir: str) -> list:
    records = []
    for name in sorted(os.listdir(gal_dir)):
        m = ISSUE_RE.match(name)
        if not m:
            continue
        iso = m.group(1)
        path = os.path.join(gal_dir, name)
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            records.extend(extract_items(text, iso))
        except Exception as exc:  # never let one bad page kill the index
            print(f"[build_search] skip {name}: {exc}", file=sys.stderr)
    # Deterministic order: newest issue first, item number ascending within a
    # date. (date is a fixed-width ISO string so plain string sort is correct.)
    def item_num(r):
        m = re.search(r"#item-(\d+)$", r["url"])
        return int(m.group(1)) if m else 0

    records.sort(key=lambda r: (r["date"], -item_num(r)), reverse=True)
    return records


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the daily digest search index")
    ap.add_argument("--dir", default=HERE, help="gallery dir with issue pages")
    ap.add_argument(
        "--out", default=None,
        help="output search-index.json path (default: <dir>/assets/search-index.json)",
    )
    args = ap.parse_args(argv)

    gal_dir = os.path.abspath(args.dir)
    out_path = args.out or os.path.join(gal_dir, "assets", "search-index.json")

    records = build_index(gal_dir)
    if not records:
        print("[build_search] no items extracted; not writing", file=sys.stderr)
        return 1

    # Compact JSON (no spaces), unicode kept (CJK content). Atomic write so a
    # crash mid-way never leaves a partial / invalid JSON file.
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    payload = json.dumps(records, ensure_ascii=False, separators=(",", ":"))
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(payload)
    os.replace(tmp, out_path)

    dates = sorted({r["date"] for r in records}, reverse=True)
    print(
        f"[build_search] wrote {out_path}: {len(records)} items "
        f"across {len(dates)} issue(s) {dates} ({len(payload)} bytes)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
