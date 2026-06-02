#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "markdown>=3.5",
#   "pygments>=2.17",
#   "weasyprint>=62",
# ]
# ///
"""Render a daily-brief markdown file into HTML (and optionally PDF).

Brand and domain are read from dha_config / .env — nothing is hardcoded here.

Style aim: Stratechery / Hacker News digest — text-dense, restrained, no banners
or gradients. Theme is "Modern Minimalist" (charcoal on near-white, serif body).

Usage:
    ./md_to_html.py input.md output.html
    ./md_to_html.py input.md                     # writes alongside as input.html
    ./md_to_html.py input.md output.html --pdf output.pdf
"""
from __future__ import annotations

import argparse
import re
import sys
import os as _os
from pathlib import Path

# Make dha_config importable when invoked as a standalone script.
sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import dha_config  # noqa: E402
from dha_config import branding  # noqa: E402

import markdown

CSS = """
:root {
  --charcoal: #2a2f36;
  --slate: #5e6772;
  --muted: #8a93a0;
  --line: #e3e5e8;
  --bg: #fbfaf7;
  --link: #1f4e8e;
  --link-bg: #eef3fa;
  --accent: #b94a3e;
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--charcoal);
  font: 17px/1.7 "Iowan Old Style", "Source Serif Pro", Georgia,
        "Source Han Serif SC", "Noto Serif SC", "Noto Serif CJK SC", "PingFang SC",
        "Hiragino Sans GB", "Microsoft YaHei", serif;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}
.wrap { max-width: 720px; margin: 0 auto; padding: 56px 28px 96px; }
.masthead {
  border-bottom: 1px solid var(--line);
  padding-bottom: 18px;
  margin-bottom: 36px;
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  font-family: "DejaVu Sans", -apple-system, "Helvetica Neue", system-ui, sans-serif;
  font-size: 12px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--slate);
}
.masthead .brand { font-weight: 600; color: var(--charcoal); }
h1, h2, h3, h4 {
  font-family: "DejaVu Sans", -apple-system, "Helvetica Neue", system-ui,
               "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
  color: var(--charcoal);
  font-weight: 700;
  line-height: 1.3;
  letter-spacing: -0.01em;
}
h1 {
  font-size: 30px;
  margin: 0 0 8px;
  letter-spacing: -0.02em;
}
h2 {
  font-size: 19px;
  margin: 48px 0 14px;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--line);
}
h3 { font-size: 16px; margin: 30px 0 10px; color: var(--slate); }
p { margin: 0 0 14px; }
ul, ol { margin: 0 0 18px; padding-left: 22px; }
li { margin-bottom: 8px; }
li::marker { color: var(--muted); }
a {
  color: var(--link);
  text-decoration: none;
  border-bottom: 1px solid #b8c8e0;
  padding-bottom: 1px;
  transition: background 0.15s, border-color 0.15s;
}
a:hover { background: var(--link-bg); border-bottom-color: var(--link); }
code {
  font: 0.9em/1.5 "DejaVu Sans Mono", "SF Mono", Menlo, Consolas, monospace;
  background: #efedea;
  padding: 1px 6px;
  border-radius: 3px;
  color: #4a4541;
}
strong { color: var(--charcoal); font-weight: 700; }
em { color: var(--slate); }
hr { border: 0; border-top: 1px solid var(--line); margin: 36px 0; }
blockquote {
  margin: 18px 0;
  padding: 4px 0 4px 18px;
  border-left: 3px solid var(--line);
  color: var(--slate);
}
.meta {
  font-family: "DejaVu Sans", system-ui, sans-serif;
  font-size: 13px;
  color: var(--slate);
  margin-bottom: 36px;
}
footer {
  margin-top: 64px;
  padding-top: 24px;
  border-top: 1px solid var(--line);
  font-family: "DejaVu Sans", system-ui, sans-serif;
  font-size: 12px;
  color: var(--muted);
  letter-spacing: 0.05em;
}
/* Emoji used as section markers — keep them but tone down */
h2 { display: flex; align-items: baseline; gap: 8px; }

/* Footnote references — appear as superscript [^N] in deep brief */
.footnote-ref { font-size: 0.75em; vertical-align: super; padding: 0 1px; }
.footnote { font-size: 13px; color: var(--slate); }
.footnote ol { padding-left: 18px; }
.footnote li { margin-bottom: 4px; }

/* Compact one-line count strip — replaces plain "Metadata: ..." line.
   Prime top space stays for content; pipeline health (fetch fails etc.)
   lives in the footer instead. Inline (not CSS grid) — weasyprint's grid
   support is partial and collapsed the old cards to a vertical column. */
.stats {
  margin: -2px 0 30px;
  font-family: "DejaVu Sans", -apple-system, system-ui, sans-serif;
  font-size: 12.5px;
  line-height: 1.9;
  color: var(--slate);
}
.stats .cat { white-space: nowrap; }
.stats .cat b {
  color: var(--charcoal);
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.stats .sep { color: var(--muted); padding: 0 7px; }

/* Lead block — "今日重点" highlights pinned above the categorized sections. */
.highlights {
  margin: 2px 0 38px;
  padding: 2px 22px 10px;
  border-left: 3px solid var(--charcoal);
  background: #f3f1ec;
  border-radius: 0 3px 3px 0;
}
.highlights h2 {
  border-bottom: none;
  margin: 16px 0 9px;
  padding: 0;
  font-size: 13px;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--slate);
}
.highlights ul { margin: 0; }
.highlights li { margin-bottom: 8px; }
.highlights li::marker { color: var(--charcoal); }
.highlights strong { color: var(--charcoal); }
@media print {
  .highlights { background: #f3f1ec; border-left-color: #444; }
}

/* Print-only rules for PDF */
@page {
  size: A4;
  margin: 18mm 16mm 22mm;
  @bottom-center {
    content: "{brand_print} · " counter(page) " / " counter(pages);
    font-family: "DejaVu Sans", sans-serif;
    font-size: 9pt;
    color: #8a93a0;
  }
}
@media print {
  body { background: white; }
  .wrap { max-width: none; padding: 0; }
  a { color: var(--charcoal); border-bottom: none; }
  a::after { content: " (" attr(href) ")"; font-size: 0.78em; color: var(--slate); word-break: break-all; }
  /* don't duplicate URL for footnote refs themselves; keep the lead block clean */
  .footnote-ref a::after, .footnote-backref::after, .highlights a::after { content: ""; }
  h2 { page-break-after: avoid; }
  li, p { page-break-inside: avoid; }
}
"""

HTML_TMPL = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<div class="wrap">
<header class="masthead">
  <span class="brand">{brand}</span>
  <span>{date}</span>
</header>
{body}
<footer>generated by daily-harness-analysis · miniflux + rsshub + {llm_cli}</footer>
</div>
</body>
</html>
"""


def extract_title_and_date(md_text: str) -> tuple[str, str]:
    """Pull H1 + a YYYY-MM-DD anywhere in the first 200 chars."""
    title_match = re.search(r"^#\s+(.+)$", md_text, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else branding()["brand"]
    date_match = re.search(r"\d{4}-\d{2}-\d{2}", md_text[:300])
    date = date_match.group(0) if date_match else ""
    return title, date


def format_masthead_date(iso: str) -> str:
    """`2026-05-29` → `5.29 早报` (masthead label). Non-ISO input → returned as-is."""
    m = re.match(r"\d{4}-(\d{2})-(\d{2})", iso)
    if not m:
        return iso
    return f"{int(m.group(1))}.{int(m.group(2))} 早报"


# (short label shown in the strip, [aliases matched in the Metadata line]).
# digest writes "Projects", deep writes "Open Source / Projects" — match both.
CATEGORY_SPECS = [
    ("Papers",   ["Papers"]),
    ("Projects", ["Open Source / Projects", "Projects"]),
    ("News",     ["Industry News"]),
    ("Blogs",    ["Blog Posts"]),
    ("GitHub",   ["GitHub Trending"]),
]


def parse_metadata(md_text: str) -> tuple[dict | None, str]:
    """Pull the `Metadata: ...` line (if any) and return (stats_dict, md_without_that_line).

    Recognized fragments:
      `Papers 15` `Open Source / Projects 12` ...  → category counts
      `total 58 links`                              → total
      `deep reading completion 54/58 (93.1%)`       → done/total + percent
      `fetch failed 4`                              → failures
    """
    m = re.search(r"^\s*(?:\*\*)?Metadata(?:\*\*)?:\s*(.+)$", md_text, re.MULTILINE)
    if not m:
        return None, md_text
    line = m.group(1)
    stats: dict = {"categories": [], "total": None, "done": None, "done_total": None,
                   "percent": None, "failed": None}
    for display, aliases in CATEGORY_SPECS:
        for alias in aliases:
            mm = re.search(rf"{re.escape(alias)}\s*:?\s*(\d+)", line)
            if mm:
                stats["categories"].append((display, int(mm.group(1))))
                break
    mt = re.search(r"total\s+(\d+)\s+links", line)
    if mt:
        stats["total"] = int(mt.group(1))
    md_done = re.search(r"completion\s+(\d+)\s*/\s*(\d+)\s*\(([0-9.]+)\s*%\)", line)
    if md_done:
        stats["done"] = int(md_done.group(1))
        stats["done_total"] = int(md_done.group(2))
        stats["percent"] = float(md_done.group(3))
    mf = re.search(r"fetch\s+failed\s+(\d+)", line)
    if mf:
        stats["failed"] = int(mf.group(1))
    md_text = md_text.replace(m.group(0), "<!-- METADATA STATS PLACEHOLDER -->")
    return stats, md_text


def render_stats_block(stats: dict) -> str:
    """Compact one-line count strip: `12 Papers · 12 Open Source · …`.

    Reader-facing counts only. Pipeline health (deep-read completion, fetch
    failures) is demoted to the footer via `render_pipeline_note`.
    """
    if not stats or not stats["categories"]:
        return ""
    parts = [
        f'<span class="cat"><b>{n}</b> {label}</span>'
        for label, n in stats["categories"]
    ]
    if stats["total"] is not None:
        parts.append(f'<span class="cat"><b>{stats["total"]}</b> total</span>')
    sep = '<span class="sep">·</span>'
    return f'<div class="stats">{sep.join(parts)}</div>'


def footnote_to_inline_link(md_text: str) -> str:
    """Rewrite `[Title][^N]` → `[Title](URL)[^N]` using URLs from `[^N]: ...` defs.

    Makes the title itself a clickable hyperlink (current style of codex output
    leaves only the tiny [^N] superscript clickable). Footnote stays for
    reference + back-nav.
    """
    footnote_def_re = re.compile(r"^\[\^(\d+)\]:\s*(.+)$", re.MULTILINE)
    url_re = re.compile(r"(https?://[^\s)]+)")
    fn_map: dict[str, str] = {}
    for m in footnote_def_re.finditer(md_text):
        n = m.group(1)
        u = url_re.search(m.group(2))
        if u:
            fn_map[n] = u.group(1).rstrip(".,);")

    def rep(m: re.Match) -> str:
        title = m.group(1)
        n = m.group(2)
        if n in fn_map:
            return f"[{title}]({fn_map[n]})[^{n}]"
        return m.group(0)

    # [Title][^N] but not [Title](url) — group 1 = title text without nested brackets
    return re.sub(r"\[([^\[\]]+?)\]\[\^(\d+)\]", rep, md_text)


def wrap_highlights(body_html: str) -> str:
    """Wrap the `今日重点 / Today's Highlights` section (its <h2> + first list)
    in `<section class="highlights">` so it renders as a styled lead block."""
    pat = re.compile(
        r'(<h2[^>]*>\s*(?:今日重点|Today\'?s Highlights)\s*</h2>\s*<ul>.*?</ul>)',
        re.IGNORECASE | re.DOTALL,
    )
    return pat.sub(r'<section class="highlights">\1</section>', body_html, count=1)


def render(md_text: str) -> str:
    b = branding()
    brand = b["brand"]
    llm_cli = _os.environ.get("LLM_CLI", "codex")

    title, date = extract_title_and_date(md_text)
    # Strip the H1 from body — it becomes <title>; masthead shows date
    md_body = re.sub(r"^#\s+.*\n", "", md_text, count=1)
    md_body = footnote_to_inline_link(md_body)
    stats, md_body = parse_metadata(md_body)
    body_html = markdown.markdown(
        md_body,
        extensions=["extra", "sane_lists", "toc", "footnotes"],
        output_format="html5",
    )
    if stats:
        body_html = body_html.replace(
            "<!-- METADATA STATS PLACEHOLDER -->", render_stats_block(stats), 1
        )
    body_html = wrap_highlights(body_html)
    # Inject brand into the CSS @page footer (CSS variables are not supported
    # in @page content on WeasyPrint, so we do a literal string substitution).
    css = CSS.replace("{brand_print}", brand.replace('"', '\\"'))
    return HTML_TMPL.format(
        title=title,
        date=format_masthead_date(date),
        css=css,
        body=body_html,
        brand=brand,
        llm_cli=llm_cli,
    )


def render_pdf(html: str, pdf_path: Path) -> None:
    from weasyprint import HTML  # lazy import; only when needed

    HTML(string=html).write_pdf(str(pdf_path))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=Path)
    ap.add_argument("output", type=Path, nargs="?")
    ap.add_argument("--pdf", type=Path, default=None, help="Also render PDF to this path")
    args = ap.parse_args()

    md_text = args.input.read_text()
    html = render(md_text)

    out_path = args.output or args.input.with_suffix(".html")
    out_path.write_text(html)
    print(f"Rendered: {out_path} ({out_path.stat().st_size:,} bytes)", file=sys.stderr)

    if args.pdf:
        render_pdf(html, args.pdf)
        print(f"Rendered: {args.pdf} ({args.pdf.stat().st_size:,} bytes)", file=sys.stderr)


if __name__ == "__main__":
    main()
