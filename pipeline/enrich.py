#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests", "pillow"]
# ///
"""Pipeline stage: acquire ONE representative image per content item for a day-dir.

Usage:
    python3 enrich.py <day-dir>
    uv run enrich.py <day-dir>

Reads <day-dir>/05-deep.md References (`[^N]: Title ... URL`) -> item N -> URL,
classifies kind (arxiv|github|web), enriches items in the CARD sections
(Papers / Open Source / Projects / Blog Posts; mirrors md_to_html.py CATEGORY_SPECS),
falling back to all footnoted items if section detection fails.

Hybrid, deterministic-first, NON-FATAL image acquisition:
  - github : opengraph.githubassets.com/1/<owner>/<repo>  (1200x600 social card)
  - web    : <meta og:image|twitter:image> from the page HTML
  - arxiv  : extract largest real figure from the PDF via poppler (pdfimages),
             fallback to rendered page-1 cover (pdftoppm)
Each saved raster is downscaled in place to max width 720 via resize_thumb.py.

OUTPUT (date-namespaced):
  <day-dir>/media/item-<N>.<ext>
  <day-dir>/media.json   {"<N>": {"img": "assets/thumbs/<date>/item-<N>.<ext>", "kind": "<kind>"}, ...}

Idempotent + safe to re-run.
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlparse

# Make dha_config importable when invoked as a standalone script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dha_config  # noqa: E402
from dha_config import env  # noqa: E402

import requests

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
TIMEOUT = 15
# resize helper lives next to this script — self-contained, no external job-tmp dependency
RESIZE_HELPER = str(Path(__file__).resolve().parent / "resize_thumb.py")
MAX_WIDTH = 720

# Sections whose items render as image cards and should be enriched.
# Includes 今日重点 (highlights) so the prominent hero cards get images too —
# highlight-only items (not repeated in a body section) were otherwise skipped.
CARD_SECTION_HEADERS = {
    "今日重点",
    "today's highlights",
    "highlights",
    "papers",
    "open source / projects",
    "projects",
    "blog posts",
    "blog",
}

FN_DEF_RE = re.compile(r"^\[\^(\d+)\]:\s*(.+)$", re.MULTILINE)
URL_RE = re.compile(r"(https?://[^\s)]+)")
# `[Title][^N]` inline references (used to attribute items to sections)
INLINE_REF_RE = re.compile(r"\[\^(\d+)\]")


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------
def parse_references(md_text: str) -> dict[int, str]:
    """item number N -> URL, from the References footnote defs."""
    out: dict[int, str] = {}
    for m in FN_DEF_RE.finditer(md_text):
        n = int(m.group(1))
        u = URL_RE.search(m.group(2))
        if u:
            out[n] = u.group(1).rstrip(".,);")
    return out


def parse_card_items(md_text: str) -> set[int]:
    """Return footnote numbers that appear inside a CARD section's body.

    Splits the doc by `## <header>` and collects `[^N]` refs that occur under a
    card-section header (Papers / Projects / Blog Posts). The References section
    itself is excluded so its defs don't pollute attribution.
    """
    card: set[int] = set()
    # iterate sections delimited by level-2 headers
    parts = re.split(r"^##\s+(.+)$", md_text, flags=re.MULTILINE)
    # parts = [pre, header1, body1, header2, body2, ...]
    for i in range(1, len(parts) - 1, 2):
        header = parts[i].strip().lower()
        body = parts[i + 1]
        if header.startswith("references"):
            continue
        if header in CARD_SECTION_HEADERS:
            for m in INLINE_REF_RE.finditer(body):
                card.add(int(m.group(1)))
    return card


def classify(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "arxiv.org" in host:
        return "arxiv"
    if "github.com" in host:
        return "github"
    return "web"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def github_owner_repo(url: str) -> tuple[str, str] | None:
    p = urlparse(url)
    segs = [s for s in p.path.split("/") if s]
    if len(segs) >= 2:
        owner, repo = segs[0], segs[1]
        repo = re.sub(r"\.git$", "", repo)
        return owner, repo
    return None


def arxiv_id(url: str) -> str | None:
    m = re.search(r"arxiv\.org/(?:abs|pdf)/([^\s?#]+?)(?:\.pdf)?$", url)
    if m:
        return m.group(1)
    return None


def looks_like_image(content_type: str, data: bytes) -> str | None:
    """Return an extension if data is a plausible image, else None."""
    ct = (content_type or "").lower()
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:5] == b"<?xml" or data.lstrip()[:4] == b"<svg":
        if "svg" in ct:
            return ".svg"
    if "svg" in ct:
        return ".svg"
    if "png" in ct:
        return ".png"
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    if "webp" in ct:
        return ".webp"
    if "gif" in ct:
        return ".gif"
    return None


def download(url: str, retries: int = 1) -> tuple[bytes, str] | None:
    """GET url; retry with linear backoff on 429/5xx/transient errors."""
    import time
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT, allow_redirects=True)
            if r.status_code == 200 and r.content:
                return r.content, r.headers.get("Content-Type", "")
            if r.status_code not in (429, 500, 502, 503, 504):
                return None
        except Exception:
            pass
        if attempt < retries:
            time.sleep(2 * (attempt + 1))
    return None


def save_raster(data: bytes, ext: str, dest_stem: Path) -> Path | None:
    """Write bytes to dest_stem+ext; downscale rasters in place. Returns path."""
    dest = dest_stem.with_suffix(ext)
    dest.write_bytes(data)
    if ext != ".svg":
        try:
            subprocess.run(
                ["python3", RESIZE_HELPER, str(dest), str(MAX_WIDTH)],
                check=False, capture_output=True, timeout=60,
            )
        except Exception:
            pass
    return dest


# ---------------------------------------------------------------------------
# per-kind acquisition
# ---------------------------------------------------------------------------
def acquire_github(url: str, dest_stem: Path) -> Path | None:
    or_ = github_owner_repo(url)
    if not or_:
        return None
    owner, repo = or_
    og = f"https://opengraph.githubassets.com/1/{owner}/{repo}"
    res = download(og, retries=2)
    if not res:
        return None
    data, ct = res
    ext = looks_like_image(ct, data)
    if not ext:
        return None
    return save_raster(data, ext, dest_stem)


def acquire_web(url: str, dest_stem: Path) -> Path | None:
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code != 200 or not r.text:
            return None
        html = r.text
        final_url = r.url
    except Exception:
        return None
    img_url = extract_og_image(html, final_url)
    if not img_url:
        return None
    res = download(img_url)
    if not res:
        return None
    data, ct = res
    ext = looks_like_image(ct, data)
    if not ext:
        return None
    return save_raster(data, ext, dest_stem)


OG_PATTERNS = [
    re.compile(r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image(?::secure_url)?["\']', re.I),
    re.compile(r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)["\']', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image(?::src)?["\']', re.I),
]


def extract_og_image(html: str, base_url: str) -> str | None:
    for pat in OG_PATTERNS:
        m = pat.search(html)
        if m:
            cand = m.group(1).strip()
            if cand:
                return urljoin(base_url, cand)
    return None


def acquire_arxiv(url: str, dest_stem: Path) -> Path | None:
    aid = arxiv_id(url)
    if not aid:
        return None
    pdf_url = f"https://arxiv.org/pdf/{aid}"
    res = download(pdf_url)
    if not res:
        return None
    data, _ = res
    if data[:4] != b"%PDF":
        return None
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        pdf_path = tdp / "paper.pdf"
        pdf_path.write_bytes(data)

        # 1) try to extract a real embedded figure
        fig = extract_best_pdf_image(pdf_path, tdp)
        if fig:
            ext = fig.suffix.lower()
            return save_raster(fig.read_bytes(), ext, dest_stem)

        # 2) fallback: render page 1 as a cover
        cover = render_pdf_cover(pdf_path, tdp)
        if cover:
            return save_raster(cover.read_bytes(), ".png", dest_stem)
    return None


def extract_best_pdf_image(pdf_path: Path, workdir: Path) -> Path | None:
    """Extract embedded raster images from pages 1-10; pick the largest sensible one."""
    prefix = workdir / "img"
    try:
        subprocess.run(
            ["pdfimages", "-png", "-f", "1", "-l", "10", str(pdf_path), str(prefix)],
            check=False, capture_output=True, timeout=90,
        )
    except Exception:
        return None
    try:
        from PIL import Image
    except Exception:
        return None
    best: tuple[int, Path] | None = None  # (area, path)
    for p in sorted(workdir.glob("img-*.png")):
        try:
            with Image.open(p) as im:
                w, h = im.size
                # reject blank/near-uniform images (e.g. all-black soft masks)
                lo, hi = im.convert("L").getextrema()
        except Exception:
            continue
        if w < 300 or h < 60:
            continue
        if hi - lo < 16:  # essentially uniform -> not a real figure
            continue
        ar = w / h if h else 999
        # skip extreme aspect ratios (banners, 1px masks, thin rules)
        if ar > 6 or ar < 0.16:
            continue
        area = w * h
        if best is None or area > best[0]:
            best = (area, p)
    return best[1] if best else None


def render_pdf_cover(pdf_path: Path, workdir: Path) -> Path | None:
    out_prefix = workdir / "cover"
    try:
        subprocess.run(
            ["pdftoppm", "-png", "-r", "110", "-f", "1", "-l", "1", str(pdf_path), str(out_prefix)],
            check=False, capture_output=True, timeout=90,
        )
    except Exception:
        return None
    cands = sorted(workdir.glob("cover*.png"))
    return cands[0] if cands else None


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    if len(sys.argv) < 2:
        print("usage: enrich.py <day-dir>", file=sys.stderr)
        return 2
    day_dir = Path(sys.argv[1]).resolve()
    deep_md = day_dir / "05-deep.md"
    if not deep_md.exists():
        print(f"error: {deep_md} not found", file=sys.stderr)
        return 1

    date_slug = day_dir.name  # e.g. 2026-05-30
    media_dir = day_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    md_text = deep_md.read_text(encoding="utf-8")
    refs = parse_references(md_text)  # N -> url
    card_items = parse_card_items(md_text)

    # enrich card-section items; fall back to all footnoted items if none detected
    if card_items:
        targets = sorted(n for n in card_items if n in refs)
    else:
        targets = sorted(refs.keys())

    media: dict[str, dict] = {}
    counts_total = {"arxiv": 0, "github": 0, "web": 0}
    counts_hit = {"arxiv": 0, "github": 0, "web": 0}
    failures: list[str] = []

    acquirers = {"arxiv": acquire_arxiv, "github": acquire_github, "web": acquire_web}

    for n in targets:
        url = refs[n]
        kind = classify(url)
        counts_total[kind] += 1
        dest_stem = media_dir / f"item-{n}"

        # idempotent: reuse existing thumb if present
        existing = next((p for p in media_dir.glob(f"item-{n}.*")), None)
        try:
            if existing and existing.stat().st_size > 0:
                saved = existing
            else:
                saved = acquirers[kind](url, dest_stem)
        except Exception as e:
            failures.append(f"[^{n}] {kind} {url} -> {type(e).__name__}: {e}")
            saved = None

        if saved and saved.exists() and saved.stat().st_size > 0:
            counts_hit[kind] += 1
            ext = saved.suffix
            media[str(n)] = {
                "img": f"assets/thumbs/{date_slug}/item-{n}{ext}",
                "kind": kind,
            }
        else:
            failures.append(f"[^{n}] {kind} {url} -> no image")

    media_json = day_dir / "media.json"
    media_json.write_text(
        json.dumps(media, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    total = len(targets)
    with_image = len(media)
    print(f"items enriched (card sections): {total}")
    print(f"got images: {with_image}/{total}")
    for k in ("arxiv", "github", "web"):
        print(f"  {k}: {counts_hit[k]}/{counts_total[k]}")
    print(f"media.json: {media_json}")
    print(f"thumb dir : {media_dir}")
    if failures:
        print("failures:")
        for f in failures:
            print(f"  {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
