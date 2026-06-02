#!/usr/bin/env bash
# ADDITIVE, NON-FATAL: render the card-site issue for one day.
#
# Usage: render_site.sh <day-dir> <YYYY-MM-DD>
#
# This is the card-gallery layer. It runs AFTER the proven md/html/pdf brief is
# built + verified. It NEVER exits non-zero on a render miss: every step is
# guarded and the script always `exit 0`. The production daily brief is never
# affected by anything that happens in here.
set -uo pipefail

# Always succeed: a trap converts any unexpected early termination into exit 0
# so this layer can never break the caller's pipeline.
trap 'exit 0' EXIT

HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=config.sh
source "$HERE/config.sh"

DAY_DIR="${1:-}"
DATE="${2:-}"

GAL="$SITE_DIR"
ENRICH="$HERE/enrich.py"
# The format-validation gate: deterministic enrich + validate + LLM CLI agentic
# fixer loop that fetches/acknowledges missing card images until validate.py
# PASSES. Itself fully non-fatal (always exit 0); see enrich_gate.sh.
ENRICH_GATE="$HERE/enrich_gate.sh"

if [[ -z "$DAY_DIR" || -z "$DATE" ]]; then
  echo "[render_site] usage: render_site.sh <day-dir> <YYYY-MM-DD> (skipped)"
  exit 0
fi
if [[ ! -d "$DAY_DIR" ]]; then
  echo "[render_site] day-dir not found: $DAY_DIR (skipped)"
  exit 0
fi
if [[ ! -d "$GAL" ]]; then
  echo "[render_site] gallery dir not found: $GAL (skipped)"
  exit 0
fi

DEEP_MD="$DAY_DIR/05-deep.md"
MEDIA_JSON="$DAY_DIR/media.json"
ISSUE_HTML="$GAL/$DATE.html"
INDEX_HTML="$GAL/index.html"

if [[ ! -s "$DEEP_MD" ]]; then
  echo "[render_site] $DEEP_MD missing/empty, nothing to render (skipped)"
  exit 0
fi

# (b) FORMAT-VALIDATION GATE: deterministic enrich + validate + Codex agentic
# fixer loop, so every card item is imaged OR explicitly acknowledged before we
# render (no missing images, no malformed media.json/05-deep.md). enrich_gate.sh
# is itself fully non-fatal (always exits 0): if it can't reach a clean PASS,
# build.py still renders fallback tiles for anything left, so this never gates
# or breaks the render. Runs every time (cheap when already complete: the gate's
# internal validate.py short-circuits before Codex if the day already PASSES).
if [[ -x "$ENRICH_GATE" || -f "$ENRICH_GATE" ]]; then
  echo "[render_site] running format-validation gate (enrich_gate.sh, non-fatal)"
  bash "$ENRICH_GATE" "$DAY_DIR" || echo "[render_site] enrich_gate non-fatal failure, continuing"
elif [[ ! -f "$MEDIA_JSON" ]]; then
  # Fallback if the gate script is unavailable: at least run the deterministic
  # enrich so a fresh day still gets thumbnails.
  echo "[render_site] enrich_gate.sh missing -> running plain enrich (non-fatal)"
  python3 "$ENRICH" "$DAY_DIR" || echo "[render_site] enrich failed, continuing without media"
fi

# (c) copy thumbs into the served, date-namespaced dir (matches the media.json
# img paths: assets/thumbs/<date>/item-N.<ext>).
mkdir -p "$GAL/assets/thumbs/$DATE"
cp "$DAY_DIR"/media/* "$GAL/assets/thumbs/$DATE/" 2>/dev/null || true

# (c2) STABLE TITLE CACHE -------------------------------------------------------
# The issue title is cached per-day in <day-dir>/.issue-title so it is BYTE-STABLE
# across re-renders (build.py's title derivation goes through codex, which is not
# deterministic, so re-runs would otherwise drift). Logic:
#   - .issue-title exists  -> pass --title "<cached>" to build.py (no codex call)
#                             AND reuse that exact title for the archive row +
#                             featured hero (they no longer each grep the HTML).
#   - .issue-title absent  -> let build.py derive it once (current behavior), then
#                             capture the derived <h1 class="issue-head__title">
#                             from the built HTML into .issue-title for next time.
TITLE_FILE="$DAY_DIR/.issue-title"
CACHED_TITLE=""
if [[ -s "$TITLE_FILE" ]]; then
  # First line only; tolerate a trailing newline. Empty cache => treat as absent.
  CACHED_TITLE="$(head -n1 "$TITLE_FILE")"
fi

# (d) build the served issue HTML. If we have a cached title, pass it so build.py
# is fully deterministic + offline-safe (no codex). Otherwise build.py derives it
# (read-only codex, already non-fatal inside build.py). --media only if present.
echo "[render_site] building $ISSUE_HTML"
BUILD_ARGS=(--src "$DEEP_MD" --out "$ISSUE_HTML" --date "$DATE")
if [[ -f "$MEDIA_JSON" ]]; then
  BUILD_ARGS+=(--media "$MEDIA_JSON")
fi
if [[ -n "$CACHED_TITLE" ]]; then
  echo "[render_site] using cached issue title: $CACHED_TITLE"
  BUILD_ARGS+=(--title "$CACHED_TITLE")
fi
python3 "$GAL/build.py" "${BUILD_ARGS[@]}" \
  || echo "[render_site] build.py failed (non-fatal)"

# Capture the derived title into the cache on the FIRST successful build only.
# We read it back from the built HTML's issue-head__title (build.py escapes it,
# so unescape via python) so the cache equals exactly what build.py emitted.
if [[ -z "$CACHED_TITLE" && -s "$ISSUE_HTML" ]]; then
  DERIVED_TITLE="$(python3 - "$ISSUE_HTML" <<'PY' 2>/dev/null || true
import re, sys, html as _html
try:
    h = open(sys.argv[1], encoding="utf-8").read()
except Exception:
    raise SystemExit(0)
m = re.search(r'class="issue-head__title">([^<]*)<', h)
if m:
    print(_html.unescape(m.group(1)).strip())
PY
)"
  if [[ -n "$DERIVED_TITLE" ]]; then
    printf '%s\n' "$DERIVED_TITLE" > "$TITLE_FILE" \
      && echo "[render_site] cached issue title -> $TITLE_FILE: $DERIVED_TITLE" \
      || echo "[render_site] could not write $TITLE_FILE (non-fatal)"
    CACHED_TITLE="$DERIVED_TITLE"
  fi
fi

if [[ ! -s "$ISSUE_HTML" ]]; then
  echo "[render_site] issue HTML not produced for $DATE; skipping index prepend"
  echo "[render_site] status: NO ISSUE HTML for $DATE"
  exit 0
fi

# (e) REGENERATE the whole landing (index.html) from ALL built issue pages.
# build_index.py parses every <YYYY-MM-DD>.html in the gallery dir (date, title,
# per-category counts + reading-time from the issue head/metastrip, and for the
# LATEST issue its top highlights from the 今日重点 / .lead block), sorts issues
# by date DESC, and emits a data-generated landing: the newest issue as the
# above-the-fold hero (title + counts + highlights + read CTA) plus a reverse-
# chronological archive. This REPLACES the old fragile regex patching of the
# .featured hero / .home-highlights / archive prepend, so the landing can never
# go stale or off-brand. It writes atomically (renders fully in-memory, then
# os.replace) so a crash never leaves a partial index.html. NON-FATAL: a build
# miss logs + leaves the previous index.html intact; the trap keeps us at exit 0.
# build_index.py emits TWO outputs from one parse: the landing (index.html) and
# the dedicated browse-all page (archive.html, served at /archive.html). We pass
# --archive-out explicitly so the archive page is regenerated on every run.
# build_index.py emits THREE outputs from one parse: the landing (index.html),
# the browse-all page (archive.html), and the 方法/how-it's-made page (how.html,
# served at /how.html). We pass --how-out so how.html is regenerated each run and
# --raw-count so its hero can surface a live "最近一次扫描 N 条" using THIS day's
# raw RSS entry count (length of $DAY_DIR/01-raw.json). The count is best-effort:
# if 01-raw.json is missing/unparseable we omit --raw-count and the page falls
# back to the qualitative "数百至上千条" range (never fabricated).
BUILD_INDEX="$GAL/build_index.py"
ARCHIVE_HTML="$GAL/archive.html"
HOW_HTML="$GAL/how.html"
RAW_JSON="$DAY_DIR/01-raw.json"
RAW_COUNT=""
if [[ -s "$RAW_JSON" ]]; then
  RAW_COUNT="$(python3 - "$RAW_JSON" <<'PY' 2>/dev/null || true
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
    print(len(d) if isinstance(d, list) else "")
except Exception:
    pass
PY
)"
fi
if [[ -f "$BUILD_INDEX" ]]; then
  echo "[render_site] regenerating landing + archive + how via build_index.py (data-generated, non-fatal)"
  INDEX_ARGS=(--dir "$GAL" --out "$INDEX_HTML" --archive-out "$ARCHIVE_HTML" --how-out "$HOW_HTML")
  if [[ -n "$RAW_COUNT" ]]; then
    echo "[render_site] passing --raw-count $RAW_COUNT (from $RAW_JSON)"
    INDEX_ARGS+=(--raw-count "$RAW_COUNT")
  fi
  python3 "$BUILD_INDEX" "${INDEX_ARGS[@]}" \
    || echo "[render_site] build_index.py failed (non-fatal); index.html/archive.html/how.html left as-is"
else
  echo "[render_site] build_index.py not found at $BUILD_INDEX; landing not regenerated (skipped)"
fi

# Regenerate the site-wide search index across ALL issues (non-fatal).
BUILD_SEARCH="$GAL/build_search.py"
if [[ -f "$BUILD_SEARCH" ]]; then
  echo "[render_site] regenerating site search index via build_search.py (non-fatal)"
  python3 "$BUILD_SEARCH" --dir "$GAL" \
    || echo "[render_site] build_search.py failed (non-fatal)"
fi

echo "[render_site] status: rendered $ISSUE_HTML for $DATE"
exit 0
