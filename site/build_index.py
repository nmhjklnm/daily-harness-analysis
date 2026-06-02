#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regenerate daily-harness-analysis's landing page (index.html) from the built issue
pages in this directory.

The landing used to be half hand-maintained (a generic slogan hero + a hardcoded
highlights block + a fragile regex-patched archive), which left it stale and
off-brand. This script makes it fully DATA-GENERATED: it parses every
``<YYYY-MM-DD>.html`` issue page in the gallery dir and emits a landing whose
above-the-fold space shows the LATEST issue as the hero (title + date + counts +
its real top highlights + a read CTA), followed by a reverse-chronological
archive of all issues. Nothing is hand-typed, so it can never go stale.

Positioning: an automated daily digest of papers, projects and industry news,
each entry carrying its original citation.

Usage:
    python3 build_index.py                 # uses the dir this file lives in
    python3 build_index.py --dir <gallery> --out <index.html>

NON-FATAL by contract: callers (render_site.sh) run this in a non-fatal,
``trap 'exit 0'`` context. This script still tries hard to never produce a
broken landing — on a fatal parse error it exits non-zero WITHOUT having written
a partial file (it writes atomically only at the very end), so the previous
index.html is preserved.
"""

from __future__ import annotations

import argparse
import html as _html
import os
import re
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dha_config import branding

# Asset cache-bust version. Keep in sync with build.py's DEFAULT_ASSET_V so the
# landing + archive + issue pages all request the same css/js build.
ASSET_V = 18

# Brand / positioning copy. Single source of truth for the landing — all derived
# from branding() so nothing is hardcoded to one deployment.
_B = branding()
BRAND = _B["brand"]
TAGLINE = _B["tagline"]
DOMAIN = _B["domain"]
REPO_URL = _B["repo_url"]
HOME_URL = _B["home_url"]
# Footer tagline / lab line. Empty when no tagline is configured -> any element
# that would show it is omitted (see footer_html / left_rail_html).
LAB = TAGLINE
# Eyebrow: tagline if set, else the brand itself (no invented niche).
EYEBROW = TAGLINE or BRAND
# One short, generic positioning line (no deployment-specific niche).
POSITIONING = (
    "An automated daily digest of papers, projects and industry news, "
    "each entry carrying its original citation."
)
META_DESCRIPTION = (
    f"{BRAND}: an automated daily digest of papers, open-source projects, "
    "industry news, blog posts and GitHub trending, each entry carrying its "
    "original citation."
)
PAGE_TITLE = BRAND
FOOTER_TAGLINE = TAGLINE

ZH_DOW = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
EN_DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
EN_DOW_FULL = [
    "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday",
]

# Category display labels for the count tiles, in canonical P/O/I/B/T order.
HERO_CATS = [
    ("papers", "Papers"),
    ("projects", "Projects"),
    ("industry", "Industry"),
    ("blog", "Blog"),
    ("trending", "Trending"),
]

ISSUE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.html$")

esc = lambda s: _html.escape(s or "", quote=True)


def _unescape(s: str) -> str:
    return _html.unescape(s or "").strip()


def parse_metastrip(html_text: str) -> dict:
    """Return {'total': int|None, 'mins': int|None, 'counts': {cat: int}} parsed
    from the issue head's metastrip aria-label, which has the shape:

        本期 54 项 · 约 12 分钟 · 分类分布 Category distribution:
        Papers 12, Projects 12, Industry News 10, Blog Posts 10, GitHub Trending 10
    """
    out = {"total": None, "mins": None, "counts": {}}
    m = re.search(r'class="metastrip"\s+aria-label="([^"]*)"', html_text)
    if not m:
        return out
    label = _html.unescape(m.group(1))

    mt = re.search(r"本期\s*(\d+)\s*项", label)
    if mt:
        out["total"] = int(mt.group(1))
    mm = re.search(r"约\s*(\d+)\s*分钟", label)
    if mm:
        out["mins"] = int(mm.group(1))

    dist = label.split("distribution:", 1)
    body = dist[1] if len(dist) > 1 else label
    # name -> count, mapped onto canonical keys.
    name_map = [
        ("papers", r"Papers"),
        ("projects", r"(?:Open Source / )?Projects"),
        ("industry", r"Industry(?:\s*News)?"),
        ("blog", r"Blog(?:\s*Posts)?"),
        ("trending", r"GitHub\s*Trending"),
    ]
    for key, pat in name_map:
        cm = re.search(pat + r"\s+(\d+)", body)
        if cm:
            out["counts"][key] = int(cm.group(1))
    return out


def parse_title(html_text: str) -> str:
    m = re.search(r'class="issue-head__title">([^<]*)<', html_text)
    return _unescape(m.group(1)) if m else ""


def parse_highlights(html_text: str, max_n: int = 5) -> list:
    """Parse the 今日重点 / .lead block. Returns a list of dicts:
        {'title': str, 'desc': str, 'anchor': '#item-N' (or '#highlights')}
    The highlight bullet titles come from the <strong> inside each
    <p class="hero__txt">; the jump anchor from the sibling .hero__jump href.
    """
    # Isolate the lead section so we don't accidentally grab body cards.
    lead = re.search(
        r'<section class="lead[^"]*"[^>]*id="highlights".*?</section>',
        html_text, re.S,
    )
    scope = lead.group(0) if lead else html_text

    out = []
    # Each highlight: a hero__txt paragraph (title in <strong>, desc after the
    # closing </a>) followed by a hero__jump anchor.
    block_re = re.compile(
        r'<p class="hero__txt">.*?<strong>(?P<title>.*?)</strong>.*?</a>'
        r'(?P<rest>.*?)</p>'
        r'.*?<a class="hero__jump"\s+href="(?P<anchor>#[^"]+)"',
        re.S,
    )
    for m in block_re.finditer(scope):
        title = _unescape(re.sub(r"<[^>]+>", "", m.group("title")))
        rest = m.group("rest")
        # Drop the footnote-ref anchor INCLUDING its digit content (e.g.
        # <a class="fnref" ...>1</a>) so the superscript number doesn't leak into
        # the body text, then strip remaining tags + tidy the leading dash.
        rest = re.sub(r'<a class="fnref"[^>]*>.*?</a>', "", rest, flags=re.S)
        desc = re.sub(r"<[^>]+>", "", rest)
        desc = _unescape(desc)
        # Tidy leading punctuation/dash and any stray leading footnote digit.
        desc = re.sub(r"^[\s\-–—:：·\.]+", "", desc)
        desc = re.sub(r"^\d+\s*[\-–—:：·\.]\s*", "", desc).strip()
        anchor = m.group("anchor") or "#highlights"
        if title:
            out.append({"title": title, "desc": desc, "anchor": anchor})
        if len(out) >= max_n:
            break
    return out


def parse_issue(path: str, iso: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    meta = parse_metastrip(text)
    return {
        "iso": iso,
        "href": f"{iso}.html",
        "title": parse_title(text) or "Agent / Harness 日报",
        "counts": meta["counts"],
        "total": meta["total"],
        "mins": meta["mins"],
        "highlights": parse_highlights(text),
    }


def collect_issues(gal_dir: str) -> list:
    issues = []
    for name in os.listdir(gal_dir):
        m = ISSUE_RE.match(name)
        if not m:
            continue
        iso = m.group(1)
        try:
            issues.append(parse_issue(os.path.join(gal_dir, name), iso))
        except Exception as exc:  # never let one bad page kill the whole build
            print(f"[build_index] skip {name}: {exc}", file=sys.stderr)
    # Sort by date DESC (newest first).
    issues.sort(key=lambda x: x["iso"], reverse=True)
    return issues


def dow(iso: str, full: bool = False):
    try:
        d = date.fromisoformat(iso)
    except Exception:
        return ("", "")
    if full:
        return (ZH_DOW[d.weekday()], EN_DOW_FULL[d.weekday()])
    return (ZH_DOW[d.weekday()], EN_DOW[d.weekday()])


def counts_tiles_html(counts: dict) -> str:
    tiles = []
    for key, label in HERO_CATS:
        val = counts.get(key)
        n_txt = str(val) if val is not None else "—"
        tiles.append(
            f'        <div class="count"><span class="count__n">{esc(n_txt)}</span>'
            f'<span class="count__l">{esc(label)}</span></div>'
        )
    return "\n".join(tiles)


def counts_compact(counts: dict) -> str:
    """P12 · O12 · I10 · B10 · T10 style compact string for archive rows."""
    order = [("papers", "P"), ("projects", "O"), ("industry", "I"),
             ("blog", "B"), ("trending", "T")]
    parts = [f"{letter}{counts[key]}" for key, letter in order if key in counts]
    return " · ".join(parts)


def feed_entry_html(issue: dict, no: int, max_hl: int = 4) -> str:
    """One recent-issue content block for the landing's continuous feed.

    Renders a heading (No. + date + title + per-category counts, the title
    linking into the issue) followed by a few of that issue's actual highlights
    (its 今日重点 entries, each deep-linking to #item-N), then a 阅读全文 link.
    Reuses the same per-issue highlights already parsed by parse_issue().
    """
    iso = issue["iso"]
    zh_d, en_d = dow(iso)
    dow_disp = f"{iso} · {esc(zh_d)} · {esc(en_d)}" if zh_d else esc(iso)
    cstr = counts_compact(issue["counts"])
    counts_html = (
        f'      <span class="feed__counts">{esc(cstr)}</span>\n' if cstr else ""
    )

    hl_items = []
    for n, hl in enumerate(issue["highlights"][:max_hl], 1):
        anchor = hl["anchor"] if hl["anchor"].startswith("#") else "#highlights"
        href = f'{issue["href"]}{anchor}'
        body = f"<b>{esc(hl['title'])}</b>"
        if hl["desc"]:
            body += f" {esc(hl['desc'])}"
        hl_items.append(
            f'      <li><a class="feed-item" href="{esc(href)}">\n'
            f'        <span class="feed-item__idx">{n:02d}</span>\n'
            f'        <span class="feed-item__body">{body}</span>\n'
            f'      </a></li>'
        )
    hl_html = (
        '    <ul class="feed__hl">\n' + "\n".join(hl_items) + "\n    </ul>\n"
        if hl_items else ""
    )

    return (
        f'  <article class="feed__entry reveal" id="feed-{esc(iso)}">\n'
        '    <div class="feed__head">\n'
        f'      <span class="feed__no">No.{no:02d}</span>\n'
        f'      <span class="feed__date">{dow_disp}</span>\n'
        f'{counts_html}'
        f'      <h3 class="feed__title"><a href="{esc(issue["href"])}">{esc(issue["title"])}</a></h3>\n'
        '    </div>\n'
        f'{hl_html}'
        f'    <a class="feed__more" href="{esc(issue["href"])}" aria-label="阅读 {esc(iso)} 完整一刊">阅读全文\n'
        '      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14M12 5l7 7-7 7"/></svg>\n'
        '    </a>\n'
        '  </article>'
    )


def recent_feed_html(issues: list, total: int, skip_latest: bool = True) -> str:
    """Continuous content feed for the CENTER column (below the hero).

    Instead of a thin link list, each recent issue is a real content block
    (heading + a few of its actual highlights inline), so scrolling the homepage
    keeps surfacing content like a magazine feed. Newest-first; skips the latest
    issue (already the hero) and shows up to ~6 more.
    """
    start = 1 if skip_latest else 0
    entries = []
    for idx in range(start, min(len(issues), start + 6)):
        entries.append(feed_entry_html(issues[idx], no=total - idx))
    if not entries:
        return ""
    return (
        '  <section class="feed" id="recent" aria-labelledby="recent-label">\n'
        '    <p class="section-label" id="recent-label">近期刊期 · Recent issues</p>\n'
        + "\n".join(entries)
        + "\n  </section>\n"
    )


def site_stats(issues: list) -> dict:
    """Aggregate stats for the right-rail summary tiles."""
    total_issues = len(issues)
    total_items = sum((i.get("total") or 0) for i in issues)
    latest = issues[0]["iso"] if issues else ""
    return {
        "issues": total_issues,
        "items": total_items,
        "latest": latest,
    }


def left_rail_html(issues: list) -> str:
    """LEFT page rail for the landing: brand/positioning + explore nav +
    全部刊期 quick links (a short list; the full archive lives in the right rail)."""
    quick = []
    for issue in issues[:7]:
        iso = issue["iso"]
        quick.append(
            f'<li><a class="rail-link" href="{esc(issue["href"])}">'
            f'<span class="rail-link__txt">{esc(iso)}</span>'
            f'<span class="rail-link__meta">{esc(issue["title"][:14])}</span></a></li>'
        )
    quick_html = "\n".join(quick)
    # Ecosystem/home link in the Explore nav: only when an explicit home_url is
    # set (else it would just point at "/", redundant with the brand link).
    home_li = (
        '          <li><a href="' + esc(HOME_URL) + '" target="_blank" rel="noopener">'
        + (esc(TAGLINE) if TAGLINE else esc(BRAND))
        + '<svg class="ext" viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 17 17 7M9 7h8v8"/></svg></a></li>\n'
        if HOME_URL and HOME_URL != "/" else ""
    )
    source_li = (
        '          <li><a href="' + esc(REPO_URL) + '" target="_blank" rel="noopener">'
        'Source'
        '<svg class="ext" viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 17 17 7M9 7h8v8"/></svg></a></li>\n'
    )
    return (
        '  <aside class="page-rail page-rail--left">\n'
        '    <div class="rail">\n'
        '      <div class="rail__block">\n'
        f'        <p class="rail-brand__eyebrow">{esc(EYEBROW)}</p>\n'
        f'        <p class="rail-brand__pos">{esc(POSITIONING)}</p>\n'
        '      </div>\n'
        '      <div class="rail__block">\n'
        '        <p class="rail__label">浏览 · Explore</p>\n'
        '        <ul class="rail-nav">\n'
        '          <li><a href="#latest">最新一刊 · Latest issue</a></li>\n'
        '          <li><a href="#recent">近期刊期 · Recent issues</a></li>\n'
        '          <li><a href="archive.html">全部刊期 · Archive</a></li>\n'
        + home_li
        + source_li
        + '        </ul>\n'
        '      </div>\n'
        '      <div class="rail__block">\n'
        '        <p class="rail__label">全部刊期 · Issues</p>\n'
        f'        <ul class="rail-list">\n{quick_html}\n        </ul>\n'
        '      </div>\n'
        '    </div>\n'
        '  </aside>\n'
    )


def archive_teaser_html(issues: list) -> str:
    """Compact archive ENTRY for the right rail (replaces the full archive list).
    A 全部刊期 → link to the dedicated archive.html with the issue count, plus the
    2–3 most recent issues as a teaser. The full browse-all list lives on
    archive.html now, not on the landing."""
    total = len(issues)
    teaser = []
    for idx, issue in enumerate(issues[:3]):
        no = total - idx
        iso = issue["iso"]
        teaser.append(
            f'          <a class="arch-row" href="{esc(issue["href"])}">\n'
            f'            <span class="arch-row__top">\n'
            f'              <span class="arch-row__date">{esc(iso)}</span>\n'
            f'              <span class="arch-row__no">No.{no:02d}</span>\n'
            f'            </span>\n'
            f'            <span class="arch-row__title">{esc(issue["title"])}</span>\n'
            f'          </a>'
        )
    teaser_html = "\n".join(teaser)
    return (
        '      <section class="archive rail__block" id="archive" aria-labelledby="archive-label">\n'
        '        <p class="rail__label" id="archive-label">全部刊期 · Archive</p>\n'
        '        <a class="rail-allissues" href="archive.html">\n'
        f'          <span class="rail-allissues__txt">全部刊期 · All issues</span>\n'
        f'          <span class="rail-allissues__n">共 {total} 期</span>\n'
        '          <svg class="rail-allissues__arrow" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14M12 5l7 7-7 7"/></svg>\n'
        '        </a>\n'
        '        <div class="arch-list arch-list--teaser">\n'
        f'{teaser_html}\n'
        '        </div>\n'
        '      </section>\n'
    )


def right_rail_html(issues: list) -> str:
    """RIGHT page rail for the landing: stats summary + the site search + a
    COMPACT archive entry (link to archive.html + count + 2–3 recent) + 品牌生态
    (ecosystem) links. The FULL archive list now lives on archive.html."""
    stats = site_stats(issues)
    # Ecosystem home link: only when an explicit home_url is configured.
    eco_home_li = (
        '          <li><a href="' + esc(HOME_URL) + '" target="_blank" rel="noopener">'
        + (esc(TAGLINE) if TAGLINE else esc(BRAND))
        + '<svg class="ext" viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 17 17 7M9 7h8v8"/></svg></a></li>\n'
        if HOME_URL and HOME_URL != "/" else ""
    )
    return (
        '  <aside class="page-rail page-rail--right">\n'
        '    <div class="rail">\n'
        '      <div class="rail__block rail-card">\n'
        '        <p class="rail__label">概览 · At a glance</p>\n'
        '        <div class="rail-stats">\n'
        f'          <span class="rail-stat rail-stat--accent"><span class="rail-stat__n">{stats["issues"]}</span>'
        '<span class="rail-stat__l">刊期 · Issues</span></span>\n'
        f'          <span class="rail-stat"><span class="rail-stat__n">{stats["items"]}</span>'
        '<span class="rail-stat__l">条目 · Items</span></span>\n'
        '        </div>\n'
        '      </div>\n'
        '      <div class="rail__block">\n'
        '        <p class="rail__label">搜索全站 · Search</p>\n'
        '        <div class="rail-search">\n'
        '          <svg class="rail-search__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>\n'
        '          <input class="rail-search__input" type="search" id="rail-search" placeholder="搜索全站 · Search all issues" autocomplete="off" aria-label="搜索全站 · Search all issues" data-site-search>\n'
        '        </div>\n'
        '      </div>\n'
        f'{archive_teaser_html(issues)}'
        '      <nav class="rail__block" aria-label="品牌生态 · Ecosystem">\n'
        '        <p class="rail__label">生态 · Ecosystem</p>\n'
        '        <ul class="rail-nav">\n'
        + eco_home_li
        + '          <li><a href="' + esc(REPO_URL) + '" target="_blank" rel="noopener">Source<svg class="ext" viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 17 17 7M9 7h8v8"/></svg></a></li>\n'
        + '        </ul>\n'
        '      </nav>\n'
        '    </div>\n'
        '  </aside>\n'
    )


def hero_html(issue: dict, total_issues: int) -> str:
    iso = issue["iso"]
    zh_full, en_full = dow(iso, full=True)
    date_disp = f"{iso} · {en_full}" if en_full else iso
    tag_txt = f"No.{total_issues:02d}"

    # Highlights list — each links into the LATEST issue page (issue['href']).
    hl_items = []
    for n, hl in enumerate(issue["highlights"], 1):
        anchor = hl["anchor"] if hl["anchor"].startswith("#") else "#highlights"
        href = f'{issue["href"]}{anchor}'
        title = esc(hl["title"])
        desc = hl["desc"]
        # Keep the body tight: title bolded, short desc trailing.
        body = f"<b>{title}</b>"
        if desc:
            body += f" {esc(desc)}"
        hl_items.append(
            f'      <li><a class="hl-item" href="{esc(href)}">\n'
            f'        <span class="hl-item__idx">{n:02d}</span>\n'
            f'        <span class="hl-item__body">{body}</span>\n'
            f'      </a></li>'
        )
    hl_list = "\n".join(hl_items)

    counts_block = ""
    if issue["counts"]:
        counts_block = (
            '      <div class="featured__counts" aria-label="Per-category item counts">\n'
            f'{counts_tiles_html(issue["counts"])}\n'
            '      </div>\n'
        )

    hl_block = ""
    if hl_list:
        hl_block = (
            '      <p class="hero-latest__hl-label">今日重点 · Today’s Highlights</p>\n'
            '      <ul class="hl-list">\n'
            f'{hl_list}\n'
            '      </ul>\n'
        )

    # NOTE: the card is a non-interactive <div> (NOT a wrapping <a>) so the inner
    # highlight links + the CTA link are valid — no nested anchors.
    # The long positioning line now lives in the LEFT rail (rail-brand__pos);
    # the center hero keeps a short section label so it doesn't duplicate it.
    return (
        '  <section class="hero-latest" id="latest" aria-labelledby="latest-title">\n'
        '    <p class="section-label">最新一刊 · Latest issue</p>\n'
        '    <div class="featured hero-latest__card reveal">\n'
        '      <div class="featured__top">\n'
        f'        <span class="tag">{esc(tag_txt)}</span>\n'
        f'        <span class="featured__date">{esc(date_disp)}</span>\n'
        '      </div>\n'
        f'      <h2 class="featured__title" id="latest-title"><a href="{esc(issue["href"])}">{esc(issue["title"])}</a></h2>\n'
        f'{counts_block}'
        f'{hl_block}'
        f'      <a class="featured__cta" href="{esc(issue["href"])}" aria-label="阅读 {esc(iso)} 完整一刊">阅读全文\n'
        '        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14M12 5l7 7-7 7"/></svg>\n'
        '      </a>\n'
        '    </div>\n'
        '  </section>\n'
    )


def head_html(title: str, description: str, skip_target: str) -> str:
    """Shared <head> + theme/reveal inline script + skip link. Same across the
    landing and the archive page so theme/fonts/icon are identical site-wide."""
    return f"""<!doctype html>
<html lang="zh" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{esc(DOMAIN)}/">
<meta property="og:url" content="{esc(DOMAIN)}/">
<script>(function(){{var d=document.documentElement;try{{var t=localStorage.getItem('nmd-theme');d.setAttribute('data-theme',t==='light'?'light':'dark');}}catch(e){{d.setAttribute('data-theme','dark');}}try{{if(!matchMedia('(prefers-reduced-motion: reduce)').matches)d.className+=' js-reveal';}}catch(e){{}}}})();</script>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Crect width='24' height='24' rx='5' fill='%230c0d0f'/%3E%3Ccircle cx='12' cy='12' r='8' fill='none' stroke='%2382868e' stroke-width='1.4'/%3E%3Ccircle cx='12' cy='12' r='3' fill='%236ad0b4'/%3E%3C/svg%3E">
<link rel="preload" href="assets/fonts/Geist-Regular.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="assets/fonts/Geist-SemiBold.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="assets/style.css?v={ASSET_V}">
</head>
<body>
<a class="skip" href="{esc(skip_target)}">Skip to content</a>
"""


def masthead_html() -> str:
    """Shared site masthead: brand + nav (全部刊期 -> archive.html) + site search
    + a top-right "view source" repo link + theme toggle. Identical site-wide."""
    # Optional ecosystem nav-link (tagline -> home_url); omitted when no tagline.
    lab_link = (
        f'''      <a class="masthead__nav-link masthead__nav-link--lab" href="{esc(HOME_URL)}" target="_blank" rel="noopener">
        {esc(TAGLINE)}
        <svg class="masthead__ext" viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 17 17 7M9 7h8v8"/></svg>
      </a>
'''
        if TAGLINE else ""
    )
    # "View source" link, top-right (before the theme toggle), opens the repo.
    source_link = (
        f'''    <a class="site-source-link" href="{esc(REPO_URL)}" target="_blank" rel="noopener" aria-label="View source">
      <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor" aria-hidden="true"><path d="M12 .5a11.5 11.5 0 0 0-3.64 22.41c.58.11.79-.25.79-.56v-2c-3.2.7-3.88-1.36-3.88-1.36-.53-1.34-1.29-1.7-1.29-1.7-1.05-.72.08-.7.08-.7 1.16.08 1.77 1.2 1.77 1.2 1.03 1.77 2.7 1.26 3.36.96.1-.75.4-1.26.73-1.55-2.55-.29-5.24-1.28-5.24-5.69 0-1.26.45-2.29 1.2-3.1-.12-.29-.52-1.46.11-3.05 0 0 .97-.31 3.18 1.18a11 11 0 0 1 5.8 0c2.2-1.49 3.17-1.18 3.17-1.18.63 1.59.23 2.76.11 3.05.75.81 1.2 1.84 1.2 3.1 0 4.42-2.69 5.39-5.25 5.68.41.36.78 1.06.78 2.14v3.17c0 .31.21.68.8.56A11.5 11.5 0 0 0 12 .5z"/></svg>
      <span class="site-source-link__txt">Source</span>
    </a>
'''
    )
    return """<header class="masthead">
  <div class="shell masthead__row">
    <a class="brand" href=\"""" + esc(HOME_URL) + """\" aria-label=\"""" + esc(BRAND) + """ home">
      <svg class="brand__mark" viewBox="0 0 24 24" aria-hidden="true" fill="none">
        <circle class="ring" cx="12" cy="12" r="9" stroke-width="1.5"/>
        <circle class="dot" cx="12" cy="12" r="3.2"/>
      </svg>
      <span class="brand__name">""" + esc(BRAND) + """</span>
    </a>
    <nav class="masthead__nav" aria-label="站点导航 · Site">
      <a class="masthead__nav-link" href="index.html">首页</a>
      <a class="masthead__nav-link" href="archive.html">全部刊期</a>
      <a class="masthead__nav-link" href="how.html">方法</a>
""" + lab_link + """    </nav>
    <span class="masthead__spacer"></span>
    <div class="masthead__search" role="search">
      <button class="masthead__search-btn" id="site-search-toggle" type="button" aria-expanded="false" aria-controls="site-search" aria-label="搜索全站 · Search all issues">
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
      </button>
      <input class="masthead__search-input" id="site-search" type="search" placeholder="搜索全站 · Search all issues" autocomplete="off" aria-label="搜索全站 · Search all issues">
    </div>
""" + source_link + """    <button class="theme-toggle" type="button" aria-label="Switch theme">
      <svg class="icon-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>
      <svg class="icon-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4.2"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>
    </button>
  </div>
</header>
"""


def footer_html() -> str:
    """Shared site footer. The 全部刊期 link now points at archive.html."""
    # Tagline line + ecosystem home link omitted cleanly when no tagline/home.
    foot_lab = (
        '        <p class="foot__lab">' + esc(LAB) + "</p>\n" if LAB else ""
    )
    foot_home = (
        '        <a class="foot__link" href="' + esc(HOME_URL)
        + '" target="_blank" rel="noopener">'
        + (esc(LAB) if LAB else esc(BRAND)) + "</a>\n"
        if HOME_URL and HOME_URL != "/" else ""
    )
    foot_copy = "© 2026 " + (esc(LAB) if LAB else esc(BRAND))
    return """<footer class="foot">
  <div class="shell">
    <div class="foot__main">
      <div class="foot__brandcol">
        <a class="foot__brandlink" href=\"""" + esc(HOME_URL) + """\">
          <svg class="brand__mark" viewBox="0 0 24 24" aria-hidden="true" fill="none">
            <circle class="ring" cx="12" cy="12" r="9" stroke-width="1.5"/>
            <circle class="dot" cx="12" cy="12" r="3.2"/>
          </svg>
          <span class="foot__brand">""" + esc(BRAND) + """</span>
        </a>
""" + foot_lab + """        <p class="foot__pos">An automated daily digest of papers, projects and industry news, each entry carrying its original citation.</p>
      </div>
      <nav class="foot__links" aria-label="品牌生态 · Ecosystem">
        <p class="foot__links-label">生态 · Ecosystem</p>
""" + foot_home + """        <a class="foot__link" href=\"""" + esc(REPO_URL) + """\" target="_blank" rel="noopener">Source</a>
        <a class="foot__link" href="archive.html">全部刊期 · Archive</a>
        <a class="foot__link" href="how.html">方法 · How it’s made</a>
      </nav>
    </div>
    <div class="foot__bar">
      <span class="foot__mono">每日更新 · """ + esc(DOMAIN) + """</span>
      <span class="foot__copy">""" + foot_copy + """</span>
    </div>
  </div>
</footer>
"""


def arch_full_rows_html(issues: list) -> str:
    """Rich, full-width archive rows for the DEDICATED archive page (archive.html).
    Reuses the existing .issue-row markup + styles (date+dow / title / meta pills:
    No., per-category counts, reading time) so it reads consistently with the rest
    of the site. Newest-first; scales as issues grow (a plain single-column list)."""
    total = len(issues)
    rows = []
    for idx, issue in enumerate(issues):
        no = total - idx
        iso = issue["iso"]
        zh_d, en_d = dow(iso)
        cstr = counts_compact(issue["counts"])
        mins = issue.get("mins")
        meta_bits = [f'        <span class="issue-row__no">No.{no:02d}</span>']
        if cstr:
            meta_bits.append(
                f'        <span class="issue-row__counts">{esc(cstr)}</span>'
            )
        if mins:
            meta_bits.append(
                f'        <span class="issue-row__time">约 {mins} 分钟</span>'
            )
        dow_disp = (
            f'<span class="issue-row__dow">{esc(zh_d)} · {esc(en_d)}</span>'
            if zh_d else ""
        )
        rows.append(
            f'      <a class="issue-row reveal" href="{esc(issue["href"])}">\n'
            f'        <span class="issue-row__date">{esc(iso)}{dow_disp}</span>\n'
            f'        <span class="issue-row__title">{esc(issue["title"])}</span>\n'
            f'        <span class="issue-row__meta">\n'
            + "\n".join(meta_bits) + "\n"
            f'        </span>\n'
            f'      </a>'
        )
    return "\n".join(rows)


def render_archive(issues: list) -> str:
    """Dedicated browse-all page (archive.html): same header/footer/theme/search,
    a clear eyebrow + title + count, and ALL issues newest-first as rich rows.
    Centered single-column directory (no 3-col rails) so it scales gracefully."""
    if not issues:
        raise SystemExit("[build_index] no issue pages found; refusing to write empty archive")

    total = len(issues)
    rows = arch_full_rows_html(issues)
    title = f"全部刊期 · {BRAND}"
    desc = (
        f"{BRAND} 全部刊期归档：每日一刊，"
        "按日期倒序浏览全部刊期，每刊带分类条目数与阅读时长。"
    )

    return (
        head_html(title, desc, skip_target="#arch-main")
        + masthead_html()
        + """
<main>
<div class="shell">
  <section class="arch-page" id="arch-main" aria-labelledby="arch-title">
    <p class="section-label">全部刊期 · All issues</p>
    <h1 class="arch-page__title" id="arch-title">全部刊期</h1>
    <p class="arch-page__count">共 """ + str(total) + """ 期 · 按日期倒序</p>
    <div class="arch-page__list">
""" + rows + """
    </div>
  </section>
</div>
</main>
"""
        + footer_html()
        + f'\n<script src="assets/app.js?v={ASSET_V}" defer></script>\n'
        '</body>\n</html>\n'
    )


# ---------------------------------------------------------------------------
# 方法 / How-it's-made page (how.html, served at <your-domain>/how.html)
# ---------------------------------------------------------------------------

# The 5-step pipeline, using the REAL pipeline facts. Each step: number, a short
# zh-first label, an EN sublabel, and 1-2 objective sentences. No marketing.
HOW_STEPS = [
    (
        "采集", "Collect",
        "每天 07:00（北京时间）由 cron 触发，持续追踪 GitHub Trending、OpenAI、Anthropic、"
        "Hugging Face、arXiv 论文与一线作者博客等约 32 个来源，拉取最近 24 小时的更新，"
        "原始量通常在数百至上千条。",
    ),
    (
        "筛选分类", "Classify · agent",
        "由 Codex（AI agent）做一次浅层判读，按技术实质客观地把条目归入 5 个内容类型——"
        "依据是内容本身在做什么，而非编辑的个人偏好。",
    ),
    (
        "深读 + 引用", "Deep-read · agentic",
        "agent 用内置的网页工具逐条打开链接、真正读完原文，再写出原创点评（它做了什么 / 贡献 / 局限），"
        "并在文中用 [^N] 标注原始引用——不是把摘要换句话复述。",
    ),
    (
        "自动配图 + 格式校验闸门", "Enrich · validation gate",
        "自动补图（arXiv 配图 / GitHub banner / og:image），随后所有内容过一道格式校验闸门，"
        "缺图或格式异常会被拦下修复，校验通过才进入发布。",
    ),
    (
        "自动发布", "Publish · 7:00",
        "校验通过后自动生成当天一刊并发布到站点，全程无人工编辑。",
    ),
]

# 信息源 groups (tasteful grouping of the ~32 sources).
HOW_SOURCE_GROUPS = [
    ("社区 · Community", "Hacker News（best / front / show）· Lobsters · LessWrong"),
    ("论文 · Papers", "arXiv cs.AI · Hugging Face Daily Papers"),
    ("开源 · Code", "GitHub Trending（all / python / rust / typescript）"),
    ("机构 · Orgs", "OpenAI · Anthropic · Google DeepMind · Microsoft Research · Hugging Face"),
    (
        "作者 · Writers",
        "Andrej Karpathy · Demis Hassabis · Jeff Dean · Chip Huyen · Lilian Weng · Sebastian Raschka · Simon Willison · "
        "Interconnects（Nathan Lambert）· Import AI · The AI Timeline · smol.ai · TheSequence · Eugene Yan",
    ),
]

# 5 个内容类型 / The five content types. slug MUST match build.py's chip_labels
# so the swatch color here equals the per-issue distribution legend (same
# --cat color is defined in .how-cat--{slug} CSS). Each: slug, zh, en, one
# objective line on what lands in it, and an inline line-icon (path-only; the
# renderer wraps it in a stroked <svg>). Tone: sober, no marketing.
HOW_CATEGORIES = [
    (
        "papers", "论文", "Papers",
        "arXiv、Hugging Face 上的新论文——方法、模型、benchmark 与实证结果。",
        '<path d="M14 3v5h5"/><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
        '<path d="M8 13h8M8 17h6"/>',
    ),
    (
        "projects", "项目", "Open Source · Projects",
        "新发布的开源框架、工具与库——可直接上手的那一类。",
        '<path d="m16 18 6-6-6-6M8 6l-6 6 6 6"/>',
    ),
    (
        "industry", "行业", "Industry News",
        "厂商与机构的产品发布、版本更新与行业动向（OpenAI、DeepMind、MSR 等）。",
        '<circle cx="12" cy="12" r="2"/>'
        '<path d="M16.24 7.76a6 6 0 0 1 0 8.49M7.76 16.24a6 6 0 0 1 0-8.49'
        'M19.07 4.93a10 10 0 0 1 0 14.14M4.93 19.07a10 10 0 0 1 0-14.14"/>',
    ),
    (
        "blog", "博客", "Blog Posts",
        "研究者与工程师的深度长文——拆解、经验与观点，不是新闻通稿。",
        '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/>',
    ),
    (
        "trending", "热门", "GitHub Trending",
        "当日 GitHub Trending 冲榜的仓库（all / python / rust / typescript）。",
        '<path d="M23 6l-9.5 9.5-5-5L1 18"/><path d="M17 6h6v6"/>',
    ),
]

# 价值 / The value. One sober lead + three contrast tiles. Qualitative only —
# no deployment-specific dollar figures (those vary per operator/model/account).
HOW_VALUE_LEAD = (
    "这不是把摘要拼起来的聚合页。每天，背后的 agent 逐条打开、读完、再点评大量更新——"
    "<b>把数百条原始更新压缩成一份聚焦的精选</b>。你花几分钟读到的，是这些判读后的结果。"
)
HOW_VALUE_TILES = [
    ("逐条真读", "agent reads each · Deep-read", False),
    ("数百 → 精选", "替你筛读 · Read down for you", False),
    ("免费", "读者成本 · Free to read", True),
]

# 为什么不一样 / What makes it different.
HOW_DIFF = [
    ("agent 逐条真读", "agent 打开每个链接读完原文再点评，而非聚合摘要拼接。"),
    ("客观分类", "按技术实质分桶，不按个人喜好取舍。"),
    ("聚焦你的领域", "只保留与你配置的关注方向相关的有价值内容，其余跳过。"),
    ("每条可溯源", "正文点评带 [^N] 原始引用，可回到一手来源核对。"),
    ("全自动每日更新", "采集 → 分类 → 深读 → 配图校验 → 发布，每天 07:00 自动跑完。"),
]


def how_steps_html() -> str:
    cards = []
    for i, (zh, en, body) in enumerate(HOW_STEPS, 1):
        arrow = (
            '      <span class="how-step__arrow" aria-hidden="true">'
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
            'stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M12 5l7 7-7 7"/></svg>'
            '</span>\n'
            if i < len(HOW_STEPS) else ""
        )
        cards.append(
            '    <li class="how-step reveal">\n'
            f'      <span class="how-step__n">{i:02d}</span>\n'
            '      <div class="how-step__body">\n'
            f'        <h3 class="how-step__title">{esc(zh)}'
            f'<span class="how-step__en">{esc(en)}</span></h3>\n'
            f'        <p class="how-step__txt">{esc(body)}</p>\n'
            '      </div>\n'
            f'{arrow}'
            '    </li>'
        )
    return "\n".join(cards)


def how_sources_html() -> str:
    rows = []
    for label, names in HOW_SOURCE_GROUPS:
        rows.append(
            '      <div class="how-src reveal">\n'
            f'        <p class="how-src__label">{esc(label)}</p>\n'
            f'        <p class="how-src__names">{esc(names)}</p>\n'
            '      </div>'
        )
    return "\n".join(rows)


def how_diff_html() -> str:
    rows = []
    for title, body in HOW_DIFF:
        rows.append(
            '      <li class="how-diff__item reveal">\n'
            '        <svg class="how-diff__tick" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            '<path d="M20 6 9 17l-5-5"/></svg>\n'
            '        <span class="how-diff__txt">'
            f'<b>{esc(title)}</b>{esc(body)}</span>\n'
            '      </li>'
        )
    return "\n".join(rows)


def how_categories_html() -> str:
    cards = []
    for slug, zh, en, desc, icon in HOW_CATEGORIES:
        cards.append(
            f'      <div class="how-cat how-cat--{slug} reveal">\n'
            '        <span class="how-cat__icon" aria-hidden="true">'
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" '
            f'stroke-linecap="round" stroke-linejoin="round">{icon}</svg></span>\n'
            '        <div class="how-cat__body">\n'
            f'          <h3 class="how-cat__title">{esc(zh)}'
            f'<span class="how-cat__en">{esc(en)}</span></h3>\n'
            f'          <p class="how-cat__desc">{esc(desc)}</p>\n'
            '        </div>\n'
            '      </div>'
        )
    return "\n".join(cards)


def how_value_html() -> str:
    tiles = []
    for n, label, is_free in HOW_VALUE_TILES:
        cls = "how-value__tile how-value__tile--free" if is_free else "how-value__tile"
        tiles.append(
            f'        <div class="{cls}">\n'
            f'          <span class="how-value__n">{esc(n)}</span>\n'
            f'          <span class="how-value__l">{esc(label)}</span>\n'
            '        </div>'
        )
    # HOW_VALUE_LEAD carries an intentional <b> emphasis, so it is NOT escaped.
    return (
        '      <p class="how-value__lead">' + HOW_VALUE_LEAD + '</p>\n'
        '      <div class="how-value__tiles">\n'
        + "\n".join(tiles)
        + '\n      </div>'
    )


def how_stat_strip_html(latest_total) -> str:
    """Compact stat strip. Uses qualitative volume range; surfaces the latest
    issue's real item count when parsed from its HTML."""
    sel = f"约 {latest_total}" if latest_total else "约 50"
    stats = [
        ("~32", "信息源 · Sources"),
        ("数百–上千", "条 / 天 · Daily intake"),
        (sel, "条精选 · Selected"),
        ("5", "类 · Categories"),
        ("100%", "带引用 · Cited"),
    ]
    tiles = []
    for n, label in stats:
        tiles.append(
            '      <div class="how-stat">\n'
            f'        <span class="how-stat__n">{esc(n)}</span>\n'
            f'        <span class="how-stat__l">{esc(label)}</span>\n'
            '      </div>'
        )
    return "\n".join(tiles)


def render_how(issues: list, raw_count=None) -> str:
    """方法 · How-it's-made page. Same head/masthead/footer chrome as every other
    page (theme inline-script, fonts, site search, theme toggle, nav, footer).

    Tells the pipeline story with the real facts: ~32 named sources (GitHub
    Trending, OpenAI, Anthropic, Hugging Face, arXiv papers, ...) → agent classify
    → agentic deep-read with [^N] citations → auto-enrich + validation gate →
    07:00 auto-publish. Objective, sober tone — no marketing fluff.
    """
    title = f"方法 · {BRAND}"
    desc = (
        f"{BRAND} 是怎么做出来的：每天从 GitHub Trending、OpenAI、Anthropic、"
        "Hugging Face、arXiv 论文等来源、数百至上千条更新中，"
        "由 AI agent 逐条真读，提炼有价值的内容，"
        "客观分类，每条带原始引用。"
    )
    latest_total = issues[0].get("total") if issues else None

    # Live "今日扫描 N 条" line: prefer the explicit --raw-count (latest day's raw
    # entry count); fall back to the qualitative range. Never fabricate a number.
    if raw_count:
        scan_line = (
            f'      <p class="how-hero__scan">最近一次扫描 <b>{esc(str(raw_count))}</b> 条更新，'
            f'提炼出约 {esc(str(latest_total)) if latest_total else "50"} 条精选。</p>\n'
        )
    else:
        scan_line = ""

    return (
        head_html(title, desc, skip_target="#how-main")
        + masthead_html()
        + f"""
<main>
<div class="shell">
  <article class="how" id="how-main">

    <header class="how-hero">
      <p class="section-label">方法 · How it’s made</p>
      <h1 class="how-hero__title">每天，一个 AI agent 像真人一样读完上千条更新</h1>
      <p class="how-hero__lead">每天从 GitHub Trending、OpenAI、Anthropic、Hugging Face、arXiv 论文等来源的数百至上千条更新里，由 AI agent 像真人一样逐条阅读，"""
        """提炼 agent / harness / 运行时相关的有价值内容——客观分类，每条带原始引用。</p>
"""
        + scan_line
        + """    </header>

    <section class="how-stats" aria-label="管线概览 · Pipeline at a glance">
"""
        + how_stat_strip_html(latest_total)
        + """
    </section>

    <section class="how-section" aria-labelledby="how-pipe-label">
      <p class="section-label" id="how-pipe-label">管线 · The pipeline</p>
      <ol class="how-steps">
"""
        + how_steps_html()
        + """
      </ol>
    </section>

    <section class="how-section" aria-labelledby="how-cat-label">
      <p class="section-label" id="how-cat-label">内容类型 · The five content types</p>
      <p class="how-section__intro">每条更新会被客观归入下面 5 类之一——依据是它在做什么，而非编辑偏好。每期顶部的分布条用的就是这套配色。</p>
      <div class="how-cats">
"""
        + how_categories_html()
        + """
      </div>
    </section>

    <section class="how-section" aria-labelledby="how-src-label">
      <p class="section-label" id="how-src-label">信息源 · Sources</p>
      <p class="how-section__intro">直接追踪 GitHub Trending、OpenAI、Anthropic、Hugging Face、arXiv 论文与一线作者博客等约 32 个来源，每天拉取最近 24 小时的更新。</p>
      <div class="how-srcs">
"""
        + how_sources_html()
        + """
      </div>
    </section>

    <section class="how-section how-value-sec" aria-labelledby="how-val-label">
      <p class="section-label" id="how-val-label">价值 · The cost behind it</p>
      <div class="how-value">
"""
        + how_value_html()
        + """
      </div>
    </section>

    <section class="how-section" aria-labelledby="how-diff-label">
      <p class="section-label" id="how-diff-label">为什么不一样 · What makes it different</p>
      <ul class="how-diff">
"""
        + how_diff_html()
        + """
      </ul>
    </section>

  </article>
</div>
</main>
"""
        + footer_html()
        + f'\n<script src="assets/app.js?v={ASSET_V}" defer></script>\n'
        '</body>\n</html>\n'
    )


def render_index(issues: list) -> str:
    if not issues:
        raise SystemExit("[build_index] no issue pages found; refusing to write empty landing")

    latest = issues[0]
    hero = hero_html(latest, total_issues=len(issues))
    recent = recent_feed_html(issues, total=len(issues))
    left_rail = left_rail_html(issues)
    right_rail = right_rail_html(issues)

    return (
        head_html(PAGE_TITLE, META_DESCRIPTION, skip_target="#latest")
        + masthead_html()
        + f"""
<main>
<div class="shell shell--wide">
<div class="page-grid page-grid--home">
{left_rail}
  <div class="page-center">
    <div class="page-center__inner">
{hero}
{recent}
    </div>
  </div>
{right_rail}
</div>
</div>
</main>
"""
        + footer_html()
        + f'\n<script src="assets/app.js?v={ASSET_V}" defer></script>\n'
        '</body>\n</html>\n'
    )


def _atomic_write(path: str, text: str) -> None:
    """Render-fully-then-replace so a crash mid-way never leaves a partial file."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Regenerate daily-harness-analysis landing + archive")
    ap.add_argument("--dir", default=HERE, help="gallery dir with issue pages")
    ap.add_argument("--out", default=None, help="output index.html path")
    ap.add_argument(
        "--archive-out", default=None,
        help="output archive.html path (default <dir>/archive.html)",
    )
    ap.add_argument(
        "--how-out", default=None,
        help="output how.html path (default <dir>/how.html)",
    )
    ap.add_argument(
        "--raw-count", type=int, default=None,
        help="latest day's raw source entry count, surfaced as a live 今日扫描 N 条 line",
    )
    args = ap.parse_args(argv)

    gal_dir = os.path.abspath(args.dir)
    out_path = args.out or os.path.join(gal_dir, "index.html")
    archive_path = args.archive_out or os.path.join(gal_dir, "archive.html")
    how_path = args.how_out or os.path.join(gal_dir, "how.html")

    issues = collect_issues(gal_dir)
    if not issues:
        print("[build_index] no <YYYY-MM-DD>.html issues found; not writing", file=sys.stderr)
        return 1

    # One parse, two outputs: the landing (index.html) + the dedicated browse-all
    # archive page (archive.html). Both render fully in-memory before any write so
    # a crash never leaves a partial page.
    index_out = render_index(issues)
    archive_out = render_archive(issues)
    how_out = render_how(issues, raw_count=args.raw_count)
    _atomic_write(out_path, index_out)
    _atomic_write(archive_path, archive_out)
    _atomic_write(how_path, how_out)

    latest = issues[0]
    print(
        f"[build_index] wrote {out_path} + {archive_path} + {how_path}: "
        f"{len(issues)} issue(s), "
        f"latest={latest['iso']} ({len(latest['highlights'])} highlights), "
        f"title={latest['title']!r}, raw_count={args.raw_count}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
