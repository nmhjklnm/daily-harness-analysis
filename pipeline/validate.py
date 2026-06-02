#!/usr/bin/env python3
"""FORMAT-VALIDATION GATE for the the daily digest pipeline.

Usage:
    python3 validate.py <day-dir>

Checks one day's artifacts and prints an itemized report.
  exit 0  => PASS  (data is complete + well-formed, safe to render)
  exit 1  => FAIL  (one "FAIL: ..." line per problem, each naming the exact
                    item / file / field so Codex can act on it)

This tool is the contract the image-enrichment loop (enrich_gate.sh) must
satisfy: Codex keeps fetching / fixing until this exits 0.

Import-light: stdlib + Pillow (already installed). It NEVER crashes — every
file read is wrapped; a read error is itself a FAIL line, not a traceback.

Checks
  (a) 05-deep.md present + well-formed: required section headers; the
      Metadata: line parses to 5 integer counts (both "Papers 12；…。" and
      "Papers 12 条，…。" shapes); every inline [^N] in the body has a matching
      References "[^N]: … URL" entry; every reference carries a URL.
  (b) media.json present + valid JSON; keys numeric strings; each value has a
      string "img" and a "kind" in {arxiv,github,web}; every img resolves to a
      real, non-empty (>1KB) image (Pillow open OR `file` magic; .svg allowed
      by extension + non-empty). Resolution tries the served gallery path
      (SITE_DIR/<img>) AND the gate-time source <day-dir>/media/
      <basename>, passing if either is a valid image.
  (c) COVERAGE: every CARD-section item (papers / projects / blog — mirrors
      build.py CARD_SECTIONS) must EITHER have a media.json image entry OR be
      acknowledged in <day-dir>/media_fallbacks.json ({"<N>": "reason"}). Any
      card item that is neither imaged nor acknowledged => FAIL naming item N +
      its URL + kind, so Codex can fetch or acknowledge it. No silent gaps.
  (d) A summary line: counts, images N/total, acknowledged fallbacks, PASS/FAIL.
"""
from __future__ import annotations

import json
import os as _os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

# Make dha_config importable when invoked as a standalone script.
sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import dha_config  # noqa: E402
from dha_config import SITE_DIR  # noqa: E402

# --- where the served issue HTML lives; media.json img paths are relative to it
GALLERY_DIR = SITE_DIR

# Card sections that render as image cards (mirror build.py CARD_SECTIONS).
# Keyed by the lower-cased "## <header>" text in 05-deep.md.
# 今日重点 (highlights) is gated too: build.py renders it as the hero card grid,
# so those prominent cards must be imaged-or-acknowledged like any other card.
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

# Required level-2 section headers + the References header (a) must all exist.
REQUIRED_HEADERS = [
    "今日重点",
    "Papers",
    "Open Source / Projects",
    "Industry News",
    "Blog Posts",
    "GitHub Trending",
    "References",
]

# The 5 canonical Metadata categories (must each parse to an integer count).
META_CATS = [
    ("Papers", ("Papers",)),
    ("Open Source / Projects", ("Open Source / Projects", "Projects")),
    ("Industry News", ("Industry News", "Industry")),
    ("Blog Posts", ("Blog Posts", "Blog")),
    ("GitHub Trending", ("GitHub Trending", "Trending")),
]

VALID_KINDS = {"arxiv", "github", "web"}
MIN_IMG_BYTES = 1024  # >1KB

FN_DEF_RE = re.compile(r"^\[\^(\d+)\]:\s*(.+)$", re.MULTILINE)
INLINE_REF_RE = re.compile(r"\[\^(\d+)\]")
URL_RE = re.compile(r"(https?://[^\s)]+)")


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def read_text(path: Path, fails: list[str], label: str) -> str | None:
    """Read a text file; on any error append a FAIL line and return None."""
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001 - validator must never raise
        fails.append(f"{label}: cannot read {path} ({type(e).__name__}: {e})")
        return None


def classify(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "arxiv.org" in host:
        return "arxiv"
    if "github.com" in host:
        return "github"
    return "web"


def is_real_image(p: Path) -> bool:
    """True if p is a non-empty (>1KB) real RASTER image.

    .svg is REJECTED: chrome screenshots and real og:images are rasters, while a
    hand-authored SVG is the easiest way for the agentic fixer to FABRICATE a
    card from an article's title. Rejecting svg forces a genuine screenshot or an
    honest fallback acknowledgement. Rasters verified via Pillow open; if Pillow
    can't decode it, fall back to `file` magic recognizing an image. Never raises.
    """
    try:
        if not p.is_file():
            return False
        if p.stat().st_size <= MIN_IMG_BYTES:
            return False
    except Exception:
        return False
    if p.suffix.lower() == ".svg":
        return False
    # raster: Pillow first
    try:
        from PIL import Image

        with Image.open(p) as im:
            im.verify()
        return True
    except Exception:
        pass
    # fallback: `file` magic
    try:
        out = subprocess.run(
            ["file", "-b", "--mime-type", str(p)],
            capture_output=True, text=True, timeout=20,
        )
        return out.returncode == 0 and out.stdout.strip().startswith("image/")
    except Exception:
        return False


def resolve_image(img: str, day_dir: Path) -> tuple[bool, str]:
    """Resolve a media.json img path to a real image file.

    Tries, in order:
      1. served gallery path : GALLERY_DIR / img   (final render location)
      2. gate-time source    : day_dir/media/<basename>  (where enrich writes,
         before render_site.sh copies thumbs into the gallery)
    Returns (ok, detail) where detail names the path checked on failure.
    """
    candidates = [GALLERY_DIR / img, day_dir / "media" / Path(img).name]
    for c in candidates:
        if is_real_image(c):
            return True, str(c)
    return False, " | ".join(str(c) for c in candidates)


# ---------------------------------------------------------------------------
# (a) 05-deep.md structure
# ---------------------------------------------------------------------------
def parse_metadata(md: str, fails: list[str]) -> dict[str, int]:
    """Parse the Metadata: line into canonical English category -> count.

    Supports BOTH shapes:
      "Papers 12；Open Source / Projects 12；…GitHub Trending 12。"
      "Papers 12 条，Open Source / Projects 12 条，…GitHub Trending 10 条。"
    """
    line = None
    for ln in md.splitlines():
        if ln.startswith("Metadata:"):
            line = ln
            break
    if line is None:
        fails.append("05-deep.md: no 'Metadata:' line found")
        return {}
    body = line[len("Metadata:"):].strip()
    raw: dict[str, int] = {}
    for chunk in re.split(r"[；;，,]", body):
        chunk = chunk.strip()
        if not chunk:
            continue
        # label  <int>  [unit 条/项]  [trailing punct 。．.]
        m = re.match(r"(.+?)\s*(\d+)\s*[条项]?\s*[。．.]?\s*$", chunk)
        if m:
            raw[m.group(1).strip()] = int(m.group(2))

    counts: dict[str, int] = {}
    for canon, aliases in META_CATS:
        val = None
        for a in aliases:
            if a in raw:
                val = raw[a]
                break
        if val is None:
            fails.append(
                f"05-deep.md: Metadata: missing integer count for '{canon}'"
            )
        else:
            counts[canon] = val
    if len(counts) != 5:
        fails.append(
            f"05-deep.md: Metadata: parsed {len(counts)}/5 integer counts "
            f"(need exactly 5)"
        )
    return counts


def split_sections(md: str) -> list[tuple[str, str]]:
    """Return [(header_text, body_text), ...] for each '## <header>'."""
    parts = re.split(r"^##\s+(.+)$", md, flags=re.MULTILINE)
    out = []
    for i in range(1, len(parts) - 1, 2):
        out.append((parts[i].strip(), parts[i + 1]))
    return out


def check_deep_md(day_dir: Path, fails: list[str]) -> dict:
    """Check 05-deep.md. Returns {counts, refs:{N:url}, card_items:set[int]}."""
    info = {"counts": {}, "refs": {}, "card_items": set(), "ok": False}
    deep = day_dir / "05-deep.md"
    if not deep.exists():
        fails.append(f"05-deep.md: file not found at {deep}")
        return info
    md = read_text(deep, fails, "05-deep.md")
    if md is None:
        return info

    # required headers
    headers_present = {h.strip() for h, _ in split_sections(md)}
    for h in REQUIRED_HEADERS:
        if h not in headers_present:
            fails.append(f"05-deep.md: missing required section header '## {h}'")

    # metadata
    info["counts"] = parse_metadata(md, fails)

    # references: N -> url ; flag refs with no URL
    refs: dict[int, str] = {}
    for m in FN_DEF_RE.finditer(md):
        n = int(m.group(1))
        u = URL_RE.search(m.group(2))
        if not u:
            fails.append(
                f"05-deep.md: reference [^{n}] has no URL "
                f"(References entry must end with a URL)"
            )
            refs[n] = ""
        else:
            refs[n] = u.group(1).rstrip(".,);")
    info["refs"] = refs

    # body inline refs must all have a matching References def.
    # The References section's own defs are NOT body usage, so exclude it.
    body_no_refs = re.split(r"^##\s+References\s*$", md, flags=re.MULTILINE)[0]
    used = {int(x) for x in INLINE_REF_RE.findall(body_no_refs)}
    for n in sorted(used):
        if n not in refs:
            fails.append(
                f"05-deep.md: inline footnote [^{n}] used in body but has no "
                f"matching '[^{n}]: … URL' entry in References"
            )

    # card-section item numbers (papers/projects/blog), excluding References
    card: set[int] = set()
    for header, body in split_sections(md):
        h = header.lower()
        if h.startswith("references"):
            continue
        if h in CARD_SECTION_HEADERS:
            for m in INLINE_REF_RE.finditer(body):
                card.add(int(m.group(1)))
    info["card_items"] = card
    info["ok"] = True
    return info


# ---------------------------------------------------------------------------
# (b) media.json
# ---------------------------------------------------------------------------
def check_media_json(day_dir: Path, fails: list[str]) -> dict[str, dict]:
    """Validate media.json structure + every image resolves. Returns the map."""
    mj = day_dir / "media.json"
    if not mj.exists():
        fails.append(f"media.json: file not found at {mj}")
        return {}
    raw = read_text(mj, fails, "media.json")
    if raw is None:
        return {}
    try:
        data = json.loads(raw)
    except Exception as e:  # noqa: BLE001
        fails.append(f"media.json: not valid JSON ({type(e).__name__}: {e})")
        return {}
    if not isinstance(data, dict):
        fails.append("media.json: top-level value must be a JSON object")
        return {}

    clean: dict[str, dict] = {}
    for key, val in data.items():
        if not re.fullmatch(r"\d+", str(key)):
            fails.append(f"media.json: key '{key}' is not a numeric string")
            continue
        if not isinstance(val, dict):
            fails.append(f"media.json: entry '{key}' is not an object")
            continue
        img = val.get("img")
        kind = val.get("kind")
        if not isinstance(img, str) or not img:
            fails.append(f"media.json: entry '{key}' missing string 'img'")
            continue
        if kind not in VALID_KINDS:
            fails.append(
                f"media.json: entry '{key}' has invalid 'kind'={kind!r} "
                f"(must be one of arxiv|github|web)"
            )
            # keep going to still check the image
        ok, detail = resolve_image(img, day_dir)
        if not ok:
            fails.append(
                f"media.json: entry '{key}' img '{img}' does not resolve to a "
                f"real non-empty (>1KB) image (checked: {detail})"
            )
            continue
        clean[str(key)] = {"img": img, "kind": kind}
    return clean


# ---------------------------------------------------------------------------
# (c) coverage
# ---------------------------------------------------------------------------
def load_fallbacks(day_dir: Path, fails: list[str]) -> dict[str, str]:
    fb = day_dir / "media_fallbacks.json"
    if not fb.exists():
        return {}
    raw = read_text(fb, fails, "media_fallbacks.json")
    if raw is None:
        return {}
    try:
        data = json.loads(raw)
    except Exception as e:  # noqa: BLE001
        fails.append(
            f"media_fallbacks.json: not valid JSON ({type(e).__name__}: {e})"
        )
        return {}
    if not isinstance(data, dict):
        fails.append("media_fallbacks.json: top-level value must be an object")
        return {}
    out = {}
    for k, v in data.items():
        out[str(k)] = str(v)
    return out


def check_coverage(deep_info: dict, media: dict, fallbacks: dict,
                   fails: list[str]) -> tuple[int, int]:
    """Every card item must be imaged OR acknowledged. Returns (imaged, ack)."""
    card_items = deep_info.get("card_items", set())
    refs = deep_info.get("refs", {})
    imaged = 0
    acknowledged = 0
    for n in sorted(card_items):
        sn = str(n)
        if sn in media:
            imaged += 1
            continue
        if sn in fallbacks:
            acknowledged += 1
            continue
        url = refs.get(n, "")
        kind = classify(url) if url else "?"
        fails.append(
            f"coverage: card item [^{n}] has NO image entry in media.json and "
            f"is NOT acknowledged in media_fallbacks.json "
            f"(url={url or 'unknown'}, kind={kind}) -> fetch an image or "
            f"add {{\"{n}\": \"reason\"}} to media_fallbacks.json"
        )
    return imaged, acknowledged


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    if len(sys.argv) < 2:
        print("usage: validate.py <day-dir>", file=sys.stderr)
        return 1
    day_dir = Path(sys.argv[1])
    fails: list[str] = []

    print(f"=== format-validation gate: {day_dir} ===")

    if not day_dir.is_dir():
        print(f"FAIL: day-dir not found or not a directory: {day_dir}")
        print(
            f"SUMMARY: counts=n/a images=0/0 acknowledged=0 fails=1 -> FAIL"
        )
        return 1

    # (a) 05-deep.md
    deep_info = check_deep_md(day_dir, fails)
    counts = deep_info.get("counts", {})

    # (b) media.json
    media = check_media_json(day_dir, fails)

    # fallbacks (acknowledgements)
    fallbacks = load_fallbacks(day_dir, fails)

    # (c) coverage
    card_items = deep_info.get("card_items", set())
    imaged, acknowledged = check_coverage(deep_info, media, fallbacks, fails)
    total_card = len(card_items)

    # --- report -------------------------------------------------------------
    if counts:
        counts_str = " · ".join(
            f"{canon} {counts[canon]}" for canon, _ in META_CATS
            if canon in counts
        )
        print(f"counts (Metadata): {counts_str}")
    else:
        print("counts (Metadata): <unparsed>")
    print(
        f"card-section items: {total_card} "
        f"(imaged {imaged}, acknowledged {acknowledged}, "
        f"uncovered {total_card - imaged - acknowledged})"
    )
    print(f"media.json valid image entries: {len(media)}")

    if fails:
        print("")
        for f in fails:
            print(f"FAIL: {f}")

    verdict = "PASS" if not fails else "FAIL"
    counts_summary = (
        "/".join(str(counts[c]) for c, _ in META_CATS if c in counts)
        if counts else "n/a"
    )
    print("")
    print(
        f"SUMMARY: counts={counts_summary} "
        f"images={imaged}/{total_card} card-items "
        f"(media-entries={len(media)}) "
        f"acknowledged={acknowledged} fails={len(fails)} -> {verdict}"
    )
    return 0 if not fails else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 - absolute last-resort guard
        print(f"FAIL: validator internal error ({type(e).__name__}: {e})")
        print("SUMMARY: -> FAIL")
        raise SystemExit(1)
