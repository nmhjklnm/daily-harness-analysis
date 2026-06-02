#!/usr/bin/env python3
"""Generator: brief markdown -> issue HTML. Keeps content verbatim, markup 1:1.

This is a one-shot build helper (not a runtime dep). Re-run to regenerate
2026-05-29.html from the source brief. The output HTML is fully static.
"""
import re
import os
import sys
import json
import html
import argparse
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dha_config import OUTPUT_DIR, SITE_DIR, branding

# --- Defaults: neutral, derived from dha_config. render_site.sh passes explicit
# --src/--out/--media, so these argparse defaults just keep `python3 build.py`
# runnable on a fresh checkout without machine-specific paths.
DEFAULT_SRC = OUTPUT_DIR / "05-deep.md"
DEFAULT_OUT = SITE_DIR / "issue.html"
DEFAULT_MEDIA = OUTPUT_DIR / "media.json"
DEFAULT_DATE = "2026-05-29"
# When no --title is passed AND derive_title is not invoked (the frozen-default
# path), keep the original hand-written title so default runs stay identical.
DEFAULT_TITLE = "运行时评测、harness 效应与 agent skill 供应链"
# Asset cache-bust version (kept in sync with index.html refs).
DEFAULT_ASSET_V = 18

_B = branding()
BRAND = _B["brand"]
TAGLINE = _B["tagline"]
DOMAIN = _B["domain"]
REPO_URL = _B["repo_url"]
HOME_URL = _B["home_url"]

ISSUE_KICKER = BRAND

# Safe fallback title when nothing better can be derived.
FALLBACK_TITLE = "Daily digest"

# Which content sections render as image cards vs stay compact rows. Image
# cards: highlights' full entries live in papers/projects/blog, so those + the
# lead carry the visual feed. Compact: industry/trending/references stay close
# to the prior dense list so the page does not become endless.
CARD_SECTIONS = {"papers", "projects", "blog"}


def human_date(iso: str) -> str:
    """YYYY-MM-DD -> 'Saturday, May 30, 2026' (stdlib, no hardcoded string).

    On a malformed date fall back to the raw input so the build never crashes.
    """
    try:
        d = date.fromisoformat(iso.strip())
    except ValueError:
        return iso
    return d.strftime("%A, %B %-d, %Y")


def fallback_title(src_text: str) -> str:
    """Safe editorial title from the source when codex is unavailable.

    Prefer the first 今日重点 bullet's bold label; else a generic line. Never
    raises.
    """
    try:
        lines_ = src_text.splitlines()
        in_hl = False
        for ln in lines_:
            if ln.startswith("## 今日重点"):
                in_hl = True
                continue
            if in_hl:
                if ln.startswith("## "):
                    break
                m = re.search(r"\*\*\[([^\]]+)\]", ln)
                if m:
                    label = m.group(1).strip()
                    if label:
                        return label
    except Exception:
        pass
    return FALLBACK_TITLE


def derive_title(src_path: Path) -> str:
    """Concise Chinese editorial theme line via codex (read-only), non-fatal.

    Runs `codex exec` sandboxed read-only with the deep md piped on stdin and
    captures the model's last message to a tmp file. ANY failure (codex missing,
    nonzero exit, timeout, empty/over-long output) falls back to fallback_title.
    Never raises — the build must not depend on codex being present.
    """
    try:
        src_text = src_path.read_text(encoding="utf-8")
    except Exception:
        return FALLBACK_TITLE
    prompt = (
        "Read this daily tech digest markdown and return ONLY a concise "
        "6-16 character Chinese editorial theme line (no quotes, no "
        "punctuation at ends) capturing the day's main threads.\n\n"
        + src_text
    )
    try:
        with tempfile.NamedTemporaryFile(
            mode="r", suffix=".txt", delete=False, encoding="utf-8"
        ) as tf:
            tmp_path = tf.name
        try:
            proc = subprocess.run(
                [
                    os.environ.get("LLM_CLI", "codex"), "exec",
                    "--skip-git-repo-check",
                    "--sandbox", "read-only",
                    "--output-last-message", tmp_path,
                    "-",
                ],
                input=prompt,
                text=True,
                capture_output=True,
                timeout=180,
            )
            out = ""
            try:
                out = Path(tmp_path).read_text(encoding="utf-8")
            except Exception:
                out = ""
            if proc.returncode != 0 and not out.strip():
                return fallback_title(src_text)
            # tidy: first non-empty line, strip wrapping quotes / trailing punct.
            cand = ""
            for ln in out.splitlines():
                if ln.strip():
                    cand = ln.strip()
                    break
            cand = cand.strip().strip('"“”‘’\'`')
            cand = cand.rstrip("。．.,，、:：;；")
            cand = normalize_dashes(cand) if cand else cand
            # sanity bound: reject empty or absurdly long model chatter.
            if cand and len(cand) <= 40:
                return cand
            return fallback_title(src_text)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except Exception:
        return fallback_title(src_text)


# --- CLI: with no args, every value equals the frozen 2026-05-29 default. ---
parser = argparse.ArgumentParser(
    description=f"{BRAND} renderer: brief markdown -> issue HTML."
)
parser.add_argument("--src", type=Path, default=DEFAULT_SRC,
                    help="path to the 05-deep.md source brief")
parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                    help="output issue HTML path")
parser.add_argument("--media", type=Path, default=DEFAULT_MEDIA,
                    help="media.json path (item-number -> {img, kind})")
parser.add_argument("--date", default=DEFAULT_DATE,
                    help="issue date YYYY-MM-DD (human date derived from it)")
parser.add_argument("--title", default=None,
                    help="issue title; if omitted, derived via codex (or a "
                         "safe fallback)")
parser.add_argument("--asset-v", type=int, default=DEFAULT_ASSET_V,
                    help="asset cache-bust version")
args = parser.parse_args()

SRC = args.src
OUT = args.out
MEDIA_PATH = args.media
ISSUE_DATE = args.date
ISSUE_DATE_HUMAN = human_date(ISSUE_DATE)
ASSET_V = args.asset_v

# Detect the frozen-default invocation (no args at all): keep title byte-stable
# and skip the codex call so `python3 build.py` is identical + offline-safe.
_is_default_run = (
    SRC == DEFAULT_SRC and OUT == DEFAULT_OUT and MEDIA_PATH == DEFAULT_MEDIA
    and ISSUE_DATE == DEFAULT_DATE and args.title is None
    and ASSET_V == DEFAULT_ASSET_V
)

# --- media map: item number (str) -> {"img": path, "kind": arxiv|github|web} -
# Hand-curated thumbnails under assets/thumbs/. Items absent from this map fall
# back to a CSS-only media block (never a broken <img>). Tolerate a missing /
# invalid file so the build never hard-fails on a fresh checkout.
try:
    MEDIA = json.loads(MEDIA_PATH.read_text(encoding="utf-8"))
    if not isinstance(MEDIA, dict):
        MEDIA = {}
except (FileNotFoundError, ValueError, OSError):
    MEDIA = {}

# section title -> (slug, english label)
SECTION_MAP = {
    "今日重点": ("highlights", "Today's Highlights", "今日重点"),
    "Papers": ("papers", "Papers", "论文"),
    "Open Source / Projects": ("projects", "Projects", "开源 / 项目"),
    "Industry News": ("industry", "Industry News", "行业动态"),
    "Blog Posts": ("blog", "Blog Posts", "博客文章"),
    "GitHub Trending": ("trending", "GitHub Trending", "GitHub 热门"),
}

text = SRC.read_text(encoding="utf-8")
lines = text.splitlines()


def esc(s: str) -> str:
    return html.escape(s, quote=True)


# --- dash normalisation: the design skill bans em/en dash glyphs outright --
# These are typographic separators, not editorial content. Replace with a
# regular hyphen (with surrounding spaces) so meaning is preserved verbatim.
def normalize_dashes(s: str) -> str:
    s = s.replace(" — ", " - ").replace("—", " - ")
    s = s.replace(" – ", " - ").replace("–", " - ")
    return re.sub(r"\s+-\s+", " - ", s)


# --- resolve the issue title (now that normalize_dashes / fallback exist) ----
# Priority: explicit --title > frozen default (no-args run) > codex-derived >
# safe fallback. derive_title is non-fatal, so the build never depends on codex.
if args.title is not None:
    ISSUE_TITLE = args.title
elif _is_default_run:
    ISSUE_TITLE = DEFAULT_TITLE
else:
    ISSUE_TITLE = derive_title(SRC)


# --- source-domain label: bare host (no scheme/path), 'www.' stripped -------
# Used for a quiet per-item provenance badge. Empty url -> empty string.
def domain_of(url: str) -> str:
    if not url:
        return ""
    host = urlsplit(url).netloc
    if not host:
        return ""
    return host.removeprefix("www.")


# --- reading-time estimate: CJK chars + Latin word tokens ------------------
# Deterministic from source text. CJK ~400 char/min, Latin ~220 word/min.
def reading_minutes(s: str) -> int:
    cjk = len(re.findall(r"[一-鿿]", s))
    words = len(re.findall(r"[A-Za-z][A-Za-z0-9\-]*", s))
    return max(1, round(cjk / 400 + words / 220))


# --- markdown inline -> html (links, footnote refs, bold) ------------------
def render_inline(s: str) -> str:
    s = normalize_dashes(s)
    out = []
    i = 0
    n = len(s)
    while i < n:
        # footnote ref [^N]  (can sit right after a link's ]); render as sup link
        m = re.match(r"\[\^(\d+)\]", s[i:])
        if m:
            num = m.group(1)
            out.append(
                f'<a class="fnref" id="fnref-{num}" href="#ref-{num}" '
                f'aria-label="Reference {num}">{num}</a>'
            )
            i += m.end()
            continue
        # bold-wrapped link **[text](url)** -> bold link
        m = re.match(r"\*\*\[([^\]]+)\]\(([^)]+)\)\*\*", s[i:])
        if m:
            label, url = m.group(1), m.group(2)
            out.append(
                f'<a href="{esc(url)}" target="_blank" rel="noopener">'
                f"<strong>{esc(label)}</strong></a>"
            )
            i += m.end()
            continue
        # link [text](url)
        m = re.match(r"\[([^\]]+)\]\(([^)]+)\)", s[i:])
        if m:
            label, url = m.group(1), m.group(2)
            out.append(
                f'<a href="{esc(url)}" target="_blank" rel="noopener">'
                f"{esc(label)}</a>"
            )
            i += m.end()
            continue
        # bold **text**
        m = re.match(r"\*\*([^*]+)\*\*", s[i:])
        if m:
            out.append(f"<strong>{esc(m.group(1))}</strong>")
            i += m.end()
            continue
        out.append(esc(s[i]))
        i += 1
    return "".join(out)


# Parse metadata line
# Two real source shapes (both must yield the SAME canonical English keys):
#   works:  "Papers 12；Open Source / Projects 12；Industry News 9；…GitHub Trending 12。"
#           (；separator, trailing 。)
#   breaks: "Papers 12 条，Open Source / Projects 12 条，…GitHub Trending 10 条。"
#           (，separator, each count followed by a 条/项 unit word, trailing 。)
# Robust parse: split on ANY of [；;，,], then per segment match a trailing
# integer that allows an optional unit word (条/项) and trailing punctuation
# after it; the leading label text (canonical English: Papers / Open Source /
# Projects / Industry News / Blog Posts / GitHub Trending) maps to the count.
meta_line = next(l for l in lines if l.startswith("Metadata:"))
meta_clean = meta_line[len("Metadata:"):].strip()
counts = {}
for chunk in re.split(r"[；;，,]", meta_clean):
    chunk = chunk.strip()
    if not chunk:
        continue
    # label  <int>  [unit 条/项]  [trailing punct 。．.]
    m = re.match(r"(.+?)\s*(\d+)\s*[条项]?\s*[。．.]?\s*$", chunk)
    if m:
        counts[m.group(1).strip()] = int(m.group(2))

# --- Walk the doc into sections --------------------------------------------
sections = []  # list of dicts: {raw_title, slug, en, zh, items:[...]}
cur = None
in_refs = False
refs = {}  # num -> (title, url)

# Items: highlight bullets vs section items differ slightly.
def flush_section():
    global cur
    if cur is not None:
        sections.append(cur)
        cur = None


idx = 0
while idx < len(lines):
    line = lines[idx]
    if line.startswith("## "):
        title = line[3:].strip()
        if title == "References":
            flush_section()
            in_refs = True
            cur = None
            idx += 1
            continue
        flush_section()
        in_refs = False
        slug, en, zh = SECTION_MAP[title]
        cur = {"raw_title": title, "slug": slug, "en": en, "zh": zh, "items": []}
        idx += 1
        continue
    if line.startswith("# "):
        idx += 1
        continue
    if in_refs:
        m = re.match(r"\[\^(\d+)\]:\s*(.*)", line)
        if m:
            num = m.group(1)
            body = m.group(2).strip()
            # split trailing URL
            um = re.search(r"(https?://\S+)\s*$", body)
            url = um.group(1) if um else ""
            title_part = body[: um.start()].strip() if um else body
            title_part = title_part.rstrip().rstrip(".").strip()
            refs[num] = (normalize_dashes(title_part), url)
        idx += 1
        continue
    if cur is not None and line.strip().startswith("- "):
        # gather the bullet (single line in this source)
        cur["items"].append(line.strip()[2:].strip())
    idx += 1
flush_section()


# --- Parse each item into (footnote-num, title, body) ----------------------
# num drives a stable per-item anchor (#item-N) so the two-level TOC can link
# straight to a single entry. Items without a footnote stay anchor-less.
def parse_item(it):
    # The first footnote ref [^N] separates the title head from the body.
    # Robust to BOTH the reference style "[Title][^N] — body" and the
    # inline-link style "[Title](url) [^N] — body" / "**[Title](url)** [^N]"
    # / nested brackets "[[x] y](url) [^N]" that codex may emit on any day.
    fm = re.search(r"\[\^(\d+)\]", it)
    if not fm:
        return None, None, it
    num = fm.group(1)
    head = it[: fm.start()].strip()
    body = re.sub(r"^\s*[—–-]\s*", "", it[fm.end():].strip()).strip()
    head = re.sub(r"^\*\*(.*)\*\*$", r"\1", head).strip()   # unwrap bold
    head = re.sub(r"\]\([^)]*\)$", "]", head).strip()        # [label](url) -> [label]
    mlab = re.match(r"^\[(.*)\]$", head, re.S)                # strip one bracket layer
    label = (mlab.group(1) if mlab else head).strip().strip("*").strip()
    if not label:
        label = head.strip("[]* ").strip()
    return num, normalize_dashes(label), body


for s in sections:
    s["parsed"] = [parse_item(it) for it in s["items"]]
    s["mins"] = reading_minutes(" ".join(s["items"]))


# --- highlight <-> item join key (deterministic, URL-keyed) -----------------
# Every 今日重点 bullet is a markdown link **[label](url)** whose URL matches a
# References URL exactly, and every ref num is emitted as id="item-{num}". So a
# URL->num map is exactly a URL->#item-N map. Iterate refs in ascending num and
# keep the FIRST occurrence (stable if a URL ever repeats across refs).
hl_url_to_item = {}
for num in sorted(refs, key=lambda x: int(x)):
    title_part, url = refs[num]
    if url:
        hl_url_to_item.setdefault(url, num)

# featured_nums = the item numbers actually picked into 今日重点 (editor picks),
# i.e. the items whose URL appears in a highlight bullet. NOT every ref (every
# ref has a URL); only the highlights' URLs resolve to a featured item.
featured_nums = set()
for s in sections:
    if s["slug"] != "highlights":
        continue
    for it in s["items"]:
        m = re.search(r"\]\(([^)]+)\)", it)
        if m and m.group(1) in hl_url_to_item:
            featured_nums.add(hl_url_to_item[m.group(1)])


# --- Emit -------------------------------------------------------------------
# DESIGN A: 今日重点 render as BIG HERO IMAGE CARDS — large image on top, the
# highlight text below, then a 全文 ↓ jump to the full #item-N entry. The hero
# image is the matched featured item's curated thumbnail (MEDIA[num].img); if
# that item has no curated image we fall back to a styled hero box (same shape),
# so a hero is never a broken <img>. Heroes are the visual top of the feed.
def render_highlight_items(items):
    out = ['<div class="heroes" role="list">']
    for it in items:
        # deterministic join: match the bullet's first md-link URL against the
        # URL->#item-N map (same key as the in-section featured pill / jump).
        m = re.search(r"\]\(([^)]+)\)", it)
        num = hl_url_to_item.get(m.group(1)) if m else None
        # link URL for the hero image anchor: the bullet's own md-link target.
        link_url = m.group(1) if m else ""
        media = MEDIA.get(str(num)) if num is not None else None
        kind = esc(media.get("kind", "")) if media else ""
        href = esc(link_url) if link_url else "#"
        if media and media.get("img"):
            img = esc(media["img"])
            hero_media = (
                f'<a class="hero__media" data-kind="{kind}" href="{href}" '
                f'target="_blank" rel="noopener" tabindex="-1" aria-hidden="true">'
                f'<img class="hero__img" loading="lazy" decoding="async" '
                f'src="{img}" alt="" width="800" height="400"></a>'
            )
        else:
            # styled fallback hero (no curated thumbnail): kind-tinted gradient
            # box, same aspect/shape as an image hero. Infer kind from the URL.
            dom = domain_of(link_url)
            fkind = ""
            if "arxiv.org" in dom:
                fkind = "arxiv"
            elif "github.com" in dom:
                fkind = "github"
            elif dom:
                fkind = "web"
            hero_media = (
                f'<a class="hero__media hero__media--fallback" data-kind="{esc(fkind)}" '
                f'href="{href}" target="_blank" rel="noopener" tabindex="-1" '
                f'aria-hidden="true">'
                f'<span class="hero__fbdom">{esc(dom) if dom else "link"}</span></a>'
            )
        jump = ""
        if num is not None:
            jump = (
                f'<a class="hero__jump" href="#item-{num}" '
                f'aria-label="跳到本期重点全文 · Jump to full entry">全文 ↓</a>'
            )
        out.append(
            f'<article class="hero" role="listitem">'
            f'{hero_media}'
            f'<div class="hero__body">'
            f'<p class="hero__txt">{render_inline(it)}</p>'
            f'{jump}'
            f'</div></article>'
        )
    out.append("</div>")
    return "\n".join(out)


# --- media block for an image-card item ------------------------------------
# Two title initials (Latin word-initials, else first chars) for the fallback
# tile. Drives a CSS-only monogram so a card without a thumbnail still reads.
def title_initials(label: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", label)
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    if words:
        return words[0][:2].upper()
    stripped = label.strip()
    return esc(stripped[:2]) if stripped else "·"


# Source-link wrapper + lazy <img>, or a CSS-only fallback tile (domain + two
# initials over a subtle gradient) when the item has no curated thumbnail. The
# outer box is a fixed aspect ratio so mixed-size thumbs stay tidy.
def render_media(num: str, label: str, url: str) -> str:
    dom = domain_of(url)
    m = MEDIA.get(str(num)) if num is not None else None
    href = esc(url) if url else "#"
    if m and m.get("img"):
        img = esc(m["img"])
        kind = esc(m.get("kind", ""))
        return (
            f'<a class="item__media" data-kind="{kind}" href="{href}" '
            f'target="_blank" rel="noopener" tabindex="-1" aria-hidden="true">'
            f'<img class="item__img" loading="lazy" decoding="async" '
            f'src="{img}" alt="" width="320" height="200"></a>'
        )
    # fallback: domain + initials over a gradient (CSS only), kind-tinted.
    kind = ""
    if num is not None:
        # infer a kind from the domain so the fallback colour still varies.
        if "arxiv.org" in dom:
            kind = "arxiv"
        elif "github.com" in dom:
            kind = "github"
        elif dom:
            kind = "web"
    return (
        f'<a class="item__media item__media--fallback" data-kind="{esc(kind)}" '
        f'href="{href}" target="_blank" rel="noopener" tabindex="-1" '
        f'aria-hidden="true">'
        f'<span class="item__mono">{title_initials(label)}</span>'
        f'<span class="item__fbdom">{esc(dom) if dom else "link"}</span></a>'
    )


def render_section_items(parsed, slug):
    is_card = slug in CARD_SECTIONS
    cls = "items items--cards" if is_card else "items"
    out = [f'<div class="{cls}">']
    for num, label, body in parsed:
        if num is not None:
            url = refs.get(num, ("", ""))[1]
            dom = domain_of(url)
            dom_html = (
                f'<span class="item__src">{esc(dom)}</span>' if dom else ""
            )
            # editor-picked items (their URL appears in 今日重点) get a quiet pill,
            # placed as the first child of the h3 so it shares line-flow + wrapping.
            feat_html = (
                '<span class="item__feat" aria-label="本期重点 · Featured">本期重点</span>'
                if num in featured_nums
                else ""
            )
            title_html = (
                f'<h3 class="item__title">{feat_html}<a href="{esc(url)}" target="_blank" '
                f'rel="noopener">{esc(label)}</a>'
                f'<a class="fnref" id="fnref-{num}" href="#ref-{num}" '
                f'aria-label="Reference {num}">{num}</a>{dom_html}</h3>'
            )
            body_html = (
                f'<p class="item__body">{render_inline(body)}</p>' if body else ""
            )
            copy_html = (
                f'<button class="item__copy" type="button" data-anchor="item-{num}" '
                f'aria-label="复制本条链接 · Copy link to this item" hidden>'
                f'<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true">'
                f'<use href="#icon-copy"/></svg>'
                f'<span class="item__copy-txt">复制链接</span></button>'
            )
            feat_cls = " item--feat" if num in featured_nums else ""
            if is_card:
                media_html = render_media(num, label, url)
                out.append(
                    f'<article class="item item--card{feat_cls}" id="item-{num}">'
                    f'{media_html}'
                    f'<div class="item__main">{title_html}{body_html}{copy_html}</div>'
                    f'</article>'
                )
            else:
                out.append(
                    f'<article class="item" id="item-{num}">{title_html}{body_html}{copy_html}</article>'
                )
        else:
            out.append(
                f'<article class="item"><p class="item__body">{render_inline(body)}</p></article>'
            )
    out.append("</div>")
    return "\n".join(out)


# category labels (slug + bilingual) — drives the distribution bar/legend.
chip_labels = [
    ("Papers", "Papers", "papers"),
    ("Open Source / Projects", "Projects", "projects"),
    ("Industry News", "Industry News", "industry"),
    ("Blog Posts", "Blog Posts", "blog"),
    ("GitHub Trending", "GitHub Trending", "trending"),
]

# zh labels for the meta-strip category chips (compact bilingual feel: keep the
# zh word the reader scans, mirroring the section heads).
chip_zh = {
    "papers": "论文",
    "projects": "项目",
    "industry": "行业",
    "blog": "博客",
    "trending": "热门",
}

# Per-category distribution legend (vertical rail-native shape). Instead of one
# stacked horizontal bar + a separate ragged chip-wrap (which forced the reader
# to map color->category->magnitude across two disconnected widgets), each
# category gets ONE scannable row: swatch · zh label · proportional micro-bar ·
# count. The micro-bar is normalized to the MAX count so the busiest category
# fills the track and the rest read relative to it (clearer than share-of-total
# in a narrow column). --p is the fill ratio (0–100) consumed by CSS.
present = [(key, lab, slug) for key, lab, slug in chip_labels if key in counts]
dist_total = sum(counts[key] for key, _, _ in present)
dist_max = max((counts[key] for key, _, _ in present), default=0) or 1
dist_chips = []
dist_aria_parts = []
for key, lab, slug in present:
    c = counts[key]
    pct = round(c / dist_max * 100)
    zh = chip_zh.get(slug, lab)
    dist_chips.append(
        f'<li class="metastrip__chip" style="--p:{pct}">'
        f'<span class="metastrip__sw metastrip__seg--{slug}" aria-hidden="true"></span>'
        f'<span class="metastrip__cat">{esc(zh)}</span>'
        f'<span class="metastrip__track" aria-hidden="true">'
        f'<span class="metastrip__fill metastrip__seg--{slug}"></span></span>'
        f'<span class="metastrip__catn">{c}</span></li>'
    )
    dist_aria_parts.append(f"{lab} {c}")

# TOC (two-level: section -> per-item links, progressively disclosed)
content_sections = [s for s in sections]
SUB_SECTIONS = {"papers", "projects", "industry", "blog", "trending"}

# --- single compact meta strip (DESIGN B) -----------------------------------
# One tidy line near the issue head folding three former blocks into one:
#   - total items + reading-time (was the 本期速览 glance header)
#   - a slim inline distribution bar (was the standalone .distbar)
#   - small per-category label+count chips (was the .distlegend list)
# Deterministic from the source brief. total_items excludes the curated
# highlights; total_mins includes them. aria-label still summarizes the
# distribution for screen readers (the bar/chips themselves are aria-hidden).
total_items = sum(
    len(s["items"]) for s in content_sections if s["slug"] in SUB_SECTIONS
)
total_mins = max(1, sum(s["mins"] for s in content_sections))

# The metastrip lives in the RIGHT RAIL now (本期速览 + distribution). We keep a
# single element carrying class="metastrip" + aria-label="本期 N 项 · 约 M 分钟 ·
# 分类分布 …" UNCHANGED in shape, because build_index.py / build_search.py parse
# that exact attribute pair to recover per-issue totals + counts. Inside it we
# render rail-friendly stat tiles + a vertical distribution strip.
metastrip_html = ""
rail_refs_jump_html = ""
if present and dist_total > 0:
    metastrip_html = (
        '<div class="metastrip" '
        f'aria-label="本期 {total_items} 项 · 约 {total_mins} 分钟 · '
        f'分类分布 Category distribution: {esc(", ".join(dist_aria_parts))}">'
        '<div class="rail-stats">'
        f'<span class="rail-stat"><span class="rail-stat__n">{total_items}</span>'
        f'<span class="rail-stat__l">项 · Items</span></span>'
        f'<span class="rail-stat rail-stat--accent"><span class="rail-stat__n">~{total_mins}</span>'
        f'<span class="rail-stat__l">分钟 · Min read</span></span>'
        '</div>'
        '<div class="rail-dist">'
        '<p class="rail-dist__label">分类分布 · Distribution</p>'
        f'<ul class="metastrip__chips" aria-hidden="true">{"".join(dist_chips)}</ul>'
        '</div>'
        '</div>'
    )

toc_html = []
for s in content_sections:
    n = len(s["items"])
    kids = ""
    if s["slug"] in SUB_SECTIONS:
        li = []
        for num, label, body in s["parsed"]:
            if num is None:
                continue
            li.append(
                f'<li><a class="toc__sublink" href="#item-{num}">'
                f'<span class="toc__subtxt">{esc(label)}</span></a></li>'
            )
        kids = f'<ul class="toc__sub" id="toc-sub-{s["slug"]}">{"".join(li)}</ul>'
    has = " has-children" if kids else ""
    toc_html.append(
        f'<li class="toc__item{has}" data-sec="{s["slug"]}">'
        f'<a class="toc__link" href="#{s["slug"]}">'
        f'<span class="toc__txt">{esc(s["en"])}</span>'
        f'<span class="n">{n}</span></a>{kids}</li>'
    )
# References as a flat top-level entry
toc_html.append(
    '<li class="toc__item" data-sec="references">'
    '<a class="toc__link" href="#references">'
    '<span class="toc__txt">引用来源 · References</span>'
    f'<span class="n">{len(refs)}</span></a></li>'
)

# body sections
body_html = []
for s in content_sections:
    src_marker = f"<!-- SOURCE: ## {s['raw_title']} -->"
    if s["slug"] == "highlights":
        body_html.append(
            f'''{src_marker}
<section class="lead reveal" id="highlights" aria-labelledby="lead-label">
  <p class="lead__label" id="lead-label">{esc(s["zh"])} · {esc(s["en"])}</p>
  {render_highlight_items(s["items"])}
</section>'''
        )
    else:
        body_html.append(
            f'''{src_marker}
<section class="section reveal" id="{s["slug"]}" aria-labelledby="h-{s["slug"]}">
  <div class="section__head">
    <h2 class="section__title" id="h-{s["slug"]}">{esc(s["zh"])} · {esc(s["en"])}</h2>
    <span class="section__count">{len(s["items"])} 项 · {esc(s["zh"])}</span>
  </div>
  {render_section_items(s["parsed"], s["slug"])}
</section>'''
        )

# client-side filter UI (progressive enhancement) — rendered hidden so non-JS
# users never see a dead control; app.js removes [hidden] on init. One toggle
# per content section (highlights/references excluded from filtering).
filter_cats = []
for s in content_sections:
    if s["slug"] in SUB_SECTIONS:
        filter_cats.append(
            f'<button class="issue-filter__cat is-on" type="button" '
            f'data-cat="{s["slug"]}" aria-pressed="true">{esc(s["en"])} '
            f'<span class="issue-filter__catn">{len(s["items"])}</span></button>'
        )
# DESIGN B: the search + 5 category pills are collapsed behind a compact,
# keyboard-accessible toggle (搜索 / 筛选). The toggle is itself a progressive
# enhancement (rendered [hidden]; app.js reveals it on init), so non-JS users
# never see a dead control. CRITICAL: the inner .issue-filter form and every
# element the JS binds to (#issue-q, .issue-filter__cat[data-cat][aria-pressed],
# data-act controls, .issue-filter__status) are UNCHANGED — only wrapped in a
# collapsible panel that is collapsed by default and expands on toggle.
filter_html = (
    '<div class="filterbar">'
    '<button class="filterbar__toggle" type="button" '
    'aria-expanded="false" aria-controls="issue-filter-panel" hidden>'
    '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true">'
    '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>'
    '<span class="filterbar__toggle-txt">搜索 / 筛选 · Search &amp; filter</span>'
    '<svg class="filterbar__chev" viewBox="0 0 24 24" width="14" height="14" '
    'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>'
    '</button>'
    '<div class="filterbar__panel" id="issue-filter-panel">'
    '<form class="issue-filter" role="search" hidden>'
    '<input class="issue-filter__input" type="search" id="issue-q" '
    'placeholder="搜索本期 · Search this issue" autocomplete="off">'
    f'<div class="issue-filter__cats" role="group" '
    f'aria-label="按类型筛选 · Filter by type">{"".join(filter_cats)}</div>'
    '<div class="issue-filter__ctrls">'
    '<button class="issue-filter__ctrl" type="button" data-act="all">全部 · All</button>'
    '<button class="issue-filter__ctrl" type="button" data-act="clear">清除 · Clear</button>'
    "</div>"
    '<p class="issue-filter__status" aria-live="polite"></p>'
    "</form>"
    '</div>'
    '</div>'
) if filter_cats else ""

# references
ref_items = []
for num in sorted(refs, key=lambda x: int(x)):
    title_part, url = refs[num]
    url_html = (
        f'<a class="ref__url" href="{esc(url)}" target="_blank" rel="noopener">{esc(url)}</a>'
        if url
        else ""
    )
    ref_items.append(
        f'''<li class="ref" id="ref-{num}">
  <span class="ref__num"><a href="#fnref-{num}">{num}</a></span>
  <span class="ref__title">{esc(title_part)}{url_html}
    <a class="ref__back" href="#fnref-{num}" aria-label="Back to citation {num}">&#8617; 回到正文 · back to text</a>
  </span>
</li>'''
    )

refs_block = f'''<!-- SOURCE: ## References -->
<section class="section refs reveal" id="references" aria-labelledby="h-references">
  <div class="section__head">
    <h2 class="section__title" id="h-references">引用来源 · References</h2>
    <span class="section__count">{len(refs)} 条 · 引用</span>
  </div>
  <ol class="refs__list">
    {"".join(ref_items)}
  </ol>
</section>'''

# Right-rail 引用来源 quick-jump: small numbered chips linking to each #ref-N
# (and the whole References section). Fills the rail; deep-links into citations.
rail_ref_chips = "".join(
    f'<a class="rail-ref" href="#ref-{num}" aria-label="跳到引用 {num} · Reference {num}">{num}</a>'
    for num in sorted(refs, key=lambda x: int(x))
)
rail_refs_jump_html = (
    '<div class="rail__block">'
    f'<p class="rail__label"><a href="#references">引用来源 · References ({len(refs)})</a></p>'
    f'<div class="rail-refs">{rail_ref_chips}</div>'
    '</div>'
) if refs else ""

# --- 其他刊期 (other issues) for the right rail ----------------------------
# Scan the OUTPUT directory for sibling <YYYY-MM-DD>.html issue pages, newest
# first, excluding the current issue. Non-fatal: if the dir can't be listed we
# just render nothing (the rail still has the stats/refs/search). This makes the
# issue page self-aware of its neighbours without a separate index pass.
def _sibling_issues(out_path: Path, current_iso: str):
    try:
        d = out_path.resolve().parent
        names = os.listdir(d)
    except OSError:
        return []
    iso_re = re.compile(r"^(\d{4}-\d{2}-\d{2})\.html$")
    found = []
    for name in names:
        m = iso_re.match(name)
        if m and m.group(1) != current_iso:
            found.append(m.group(1))
    found.sort(reverse=True)
    return found

other_isos = _sibling_issues(OUT, ISSUE_DATE)
rail_other_issues_html = ""
if other_isos:
    rows = []
    for iso in other_isos[:8]:
        rows.append(
            f'<li><a class="rail-link" href="{esc(iso)}.html">'
            f'<span class="rail-link__txt">{esc(iso)}</span></a></li>'
        )
    rail_other_issues_html = (
        '<div class="rail__block">'
        '<p class="rail__label">其他刊期 · Other issues</p>'
        f'<ul class="rail-list">{"".join(rows)}</ul>'
        '<p class="rail__label" style="margin-top:0.7rem">'
        '<a href="archive.html">全部刊期 · All issues →</a></p>'
        '</div>'
    )
else:
    rail_other_issues_html = (
        '<div class="rail__block">'
        '<p class="rail__label"><a href="archive.html">全部刊期 · All issues →</a></p>'
        '</div>'
    )

# Right-rail search box (delegates to the shared masthead #site-search engine;
# see app.js). A real input so the rail isn't empty; on focus it loads the same
# index + dropdown logic. id is distinct from the masthead input.
rail_search_html = (
    '<div class="rail__block">'
    '<p class="rail__label">搜索全站 · Search</p>'
    '<div class="rail-search">'
    '<svg class="rail-search__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>'
    '<input class="rail-search__input" type="search" id="rail-search" '
    'placeholder="搜索全站 · Search all issues" autocomplete="off" '
    'aria-label="搜索全站 · Search all issues" data-site-search>'
    '</div>'
    '</div>'
)

# Inline head script: theme + progressive-enhancement flags, AND the saved
# grid/list view (nmd-view) applied to <html> BEFORE paint so the issue page
# never flashes the wrong layout on reload. The body container also gets the
# class from app.js on init; this just avoids the FOUC.
HEAD_SCRIPT = """(function(){var d=document.documentElement;try{var t=localStorage.getItem('nmd-theme');d.setAttribute('data-theme',t==='light'?'light':'dark');}catch(e){d.setAttribute('data-theme','dark');}d.className+=' js-toc';try{if(!matchMedia('(prefers-reduced-motion: reduce)').matches)d.className+=' js-reveal';}catch(e){}try{var v=localStorage.getItem('nmd-view');d.className+=(v==='grid')?' view-grid':' view-list';}catch(e){d.className+=' view-list';}})();"""

# Issue-feed view toggle (列表 / 网格) — a thin right-aligned toolbar above the
# feed. Keyboard accessible: a radio-style group of two aria-pressed buttons.
# app.js wires the click/persist; the inline head script applies the saved view
# (view-grid / view-list on <html>) before paint. Rendered always (no JS gate)
# but harmless without JS (clicks just do nothing; default list view stands).
VIEW_TOGGLE = '''<div class="viewbar">
      <span class="viewbar__label" id="viewbar-label">视图 · View</span>
      <div class="viewbar__group" role="group" aria-labelledby="viewbar-label">
        <button class="viewbar__btn" type="button" data-view="list" aria-pressed="true" aria-label="列表视图 · List view">
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/></svg>
          <span class="viewbar__btn-txt">列表</span>
        </button>
        <button class="viewbar__btn" type="button" data-view="grid" aria-pressed="false" aria-label="网格视图 · Grid view">
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
          <span class="viewbar__btn-txt">网格</span>
        </button>
      </div>
    </div>'''

# Single off-screen <symbol> def for the per-item copy-link icon. Referenced
# via <use href="#icon-copy"/> by all footnoted items so the SVG markup is
# defined once instead of inlined 55x (page-weight refinement, no visual change).
ICON_DEFS = (
    '<svg width="0" height="0" style="position:absolute" aria-hidden="true" '
    'focusable="false"><symbol id="icon-copy" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round">'
    '<path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1"/>'
    '<path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/>'
    '</symbol></svg>'
)

# Optional ecosystem nav-link (tagline -> home_url). Omitted entirely when the
# tagline is empty so no dangling element / divider renders.
_lab_link = (
    f'''      <a class="masthead__nav-link masthead__nav-link--lab" href="{esc(HOME_URL)}" target="_blank" rel="noopener">
        {esc(TAGLINE)}
        <svg class="masthead__ext" viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 17 17 7M9 7h8v8"/></svg>
      </a>
'''
    if TAGLINE else ""
)

# "View source" link, top-right of the header (before the theme toggle). Opens
# the project repo in a new tab. Subtle, right-aligned (see .site-source-link).
_source_link = (
    f'''    <a class="site-source-link" href="{esc(REPO_URL)}" target="_blank" rel="noopener" aria-label="View source">
      <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor" aria-hidden="true"><path d="M12 .5a11.5 11.5 0 0 0-3.64 22.41c.58.11.79-.25.79-.56v-2c-3.2.7-3.88-1.36-3.88-1.36-.53-1.34-1.29-1.7-1.29-1.7-1.05-.72.08-.7.08-.7 1.16.08 1.77 1.2 1.77 1.2 1.03 1.77 2.7 1.26 3.36.96.1-.75.4-1.26.73-1.55-2.55-.29-5.24-1.28-5.24-5.69 0-1.26.45-2.29 1.2-3.1-.12-.29-.52-1.46.11-3.05 0 0 .97-.31 3.18 1.18a11 11 0 0 1 5.8 0c2.2-1.49 3.17-1.18 3.17-1.18.63 1.59.23 2.76.11 3.05.75.81 1.2 1.84 1.2 3.1 0 4.42-2.69 5.39-5.25 5.68.41.36.78 1.06.78 2.14v3.17c0 .31.21.68.8.56A11.5 11.5 0 0 0 12 .5z"/></svg>
      <span class="site-source-link__txt">Source</span>
    </a>
'''
)

MASTHEAD = f'''<header class="masthead">
  <div class="shell masthead__row">
    <a class="brand" href="{esc(HOME_URL)}" aria-label="{esc(BRAND)} home">
      <svg class="brand__mark" viewBox="0 0 24 24" aria-hidden="true" fill="none">
        <circle class="ring" cx="12" cy="12" r="9" stroke-width="1.5"/>
        <circle class="dot" cx="12" cy="12" r="3.2"/>
      </svg>
      <span class="brand__name">{esc(BRAND)}</span>
    </a>
    <nav class="masthead__nav" aria-label="站点导航 · Site">
      <a class="masthead__nav-link" href="index.html">首页</a>
      <a class="masthead__nav-link" href="archive.html">全部刊期</a>
      <a class="masthead__nav-link" href="how.html">方法</a>
{_lab_link}    </nav>
    <span class="masthead__spacer"></span>
    <div class="masthead__search" role="search">
      <button class="masthead__search-btn" id="site-search-toggle" type="button" aria-expanded="false" aria-controls="site-search" aria-label="搜索全站 · Search all issues">
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
      </button>
      <input class="masthead__search-input" id="site-search" type="search" placeholder="搜索全站 · Search all issues" autocomplete="off" aria-label="搜索全站 · Search all issues">
    </div>
{_source_link}    <button class="theme-toggle" type="button" aria-label="Switch theme">
      <svg class="icon-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>
      <svg class="icon-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4.2"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>
    </button>
  </div>
</header>'''

# Footer tagline line — omitted when tagline is empty (no dangling element).
_foot_lab = f'        <p class="foot__lab">{esc(TAGLINE)}</p>\n' if TAGLINE else ""
# Footer "ecosystem" home link — omitted when there's no explicit home_url
# (i.e. it would just point at "/", which is redundant with the brand link).
_foot_home = (
    f'        <a class="foot__link" href="{esc(HOME_URL)}" target="_blank" rel="noopener">'
    f'{esc(TAGLINE) if TAGLINE else esc(BRAND)}</a>\n'
    if HOME_URL and HOME_URL != "/" else ""
)
_foot_copy = (
    f"© 2026 {esc(TAGLINE)}" if TAGLINE else f"© 2026 {esc(BRAND)}"
)

FOOT = f'''<footer class="foot">
  <div class="shell">
    <div class="foot__main">
      <div class="foot__brandcol">
        <a class="foot__brandlink" href="{esc(HOME_URL)}">
          <svg class="brand__mark" viewBox="0 0 24 24" aria-hidden="true" fill="none">
            <circle class="ring" cx="12" cy="12" r="9" stroke-width="1.5"/>
            <circle class="dot" cx="12" cy="12" r="3.2"/>
          </svg>
          <span class="foot__brand">{esc(BRAND)}</span>
        </a>
{_foot_lab}        <p class="foot__pos">An automated daily digest of papers, projects and industry news, each entry carrying its original citation.</p>
      </div>
      <nav class="foot__links" aria-label="品牌生态 · Ecosystem">
        <p class="foot__links-label">生态 · Ecosystem</p>
{_foot_home}        <a class="foot__link" href="{esc(REPO_URL)}" target="_blank" rel="noopener">Source</a>
        <a class="foot__link" href="archive.html">全部刊期 · Archive</a>
        <a class="foot__link" href="how.html">方法 · How it’s made</a>
      </nav>
    </div>
    <div class="foot__bar">
      <span class="foot__mono">每日更新 · {esc(DOMAIN)}</span>
      <span class="foot__copy">{_foot_copy}</span>
    </div>
  </div>
</footer>'''

html_doc = f'''<!doctype html>
<html lang="zh" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(BRAND)} {ISSUE_DATE} - {ISSUE_TITLE}</title>
<meta name="description" content="{esc(BRAND)} {ISSUE_DATE}: a daily digest of papers, open-source projects, industry news, blog posts and GitHub trending.">
<link rel="canonical" href="{esc(DOMAIN)}/{ISSUE_DATE}.html">
<meta property="og:url" content="{esc(DOMAIN)}/{ISSUE_DATE}.html">
<script>{HEAD_SCRIPT}</script>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Crect width='24' height='24' rx='5' fill='%230c0d0f'/%3E%3Ccircle cx='12' cy='12' r='8' fill='none' stroke='%2382868e' stroke-width='1.4'/%3E%3Ccircle cx='12' cy='12' r='3' fill='%236ad0b4'/%3E%3C/svg%3E">
<link rel="preload" href="assets/fonts/Geist-Regular.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="assets/fonts/Geist-SemiBold.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="assets/style.css?v={ASSET_V}">
</head>
<body>
{ICON_DEFS}
<a class="skip" href="#highlights">跳到正文 · Skip to content</a>
{MASTHEAD}
<div class="read-progress" aria-hidden="true"><span class="read-progress__bar" id="readbar"></span></div>

<main>
<div class="shell shell--wide">
<!-- SOURCE: # {BRAND} — {ISSUE_DATE} -->
<div class="issue-head">
  <a class="issue-head__back" href="archive.html">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
    全部刊期 · All issues
  </a>
  <p class="issue-head__kicker">{ISSUE_KICKER}</p>
  <p class="issue-head__date">{ISSUE_DATE} · {ISSUE_DATE_HUMAN}</p>
  <h1 class="issue-head__title">{esc(ISSUE_TITLE)}</h1>
</div>

<div class="page-grid page-grid--issue issue-layout">
  <!-- LEFT RAIL: in-issue TOC (本期目录) + category filter pills -->
  <aside class="page-rail page-rail--left">
    <div class="rail">
      <nav class="toc rail__block" aria-label="本期目录 · In this issue">
        <button class="toc__mobile" type="button" aria-expanded="false" aria-controls="toc-list">
          <span class="toc__mobile-txt">本期目录 · In this issue</span>
          <svg class="toc__chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>
        </button>
        <p class="toc__title">本期目录 · In this issue</p>
        <ul class="toc__list" id="toc-list">
          {"".join(toc_html)}
        </ul>
      </nav>
      {filter_html}
    </div>
  </aside>

  <!-- CENTER: the card feed (highlights hero + section cards + refs) -->
  <div class="page-center">
    <div class="page-center__inner issue-body">
      {VIEW_TOGGLE}
      {"".join(body_html)}
      {refs_block}
    </div>
  </div>

  <!-- RIGHT RAIL: 本期速览 stats + distribution + references + other issues + search -->
  <aside class="page-rail page-rail--right">
    <div class="rail">
      <!-- SOURCE: Metadata: ... (the metastrip element below carries the aria-label index/search parsers read) -->
      <div class="rail__block rail-card">
        <p class="rail__label">本期速览 · This issue</p>
        {metastrip_html}
      </div>
      {rail_refs_jump_html}
      {rail_other_issues_html}
      {rail_search_html}
    </div>
  </aside>
</div>
</div>
</main>

{FOOT}
<script src="assets/app.js?v={ASSET_V}" defer></script>
</body>
</html>'''

OUT.write_text(html_doc, encoding="utf-8")
print(f"Wrote {OUT} ({len(html_doc)} bytes)")
print(f"Sections: {[(s['slug'], len(s['items'])) for s in content_sections]}")
print(f"Refs: {len(refs)} (min {min(int(x) for x in refs)} .. max {max(int(x) for x in refs)})")
print(f"Counts: {counts}")
