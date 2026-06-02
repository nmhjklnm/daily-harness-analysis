#!/usr/bin/env bash
# FORMAT-VALIDATION GATE loop for the card-site layer.
#
# Usage: enrich_gate.sh <day-dir>
#
# Pipeline position: runs in render_site.sh / run.sh AFTER the proven md/html/pdf
# brief is built + verified, REPLACING the plain `enrich.py` call. It guarantees
# the day's data is complete + well-formed (no missing card images, no malformed
# media.json / 05-deep.md) BEFORE the card site renders.
#
# Flow:
#   1. deterministic enrich  : python3 enrich.py <day-dir>
#   2. gate                  : python3 validate.py <day-dir>  (PASS -> done)
#   3. if FAIL               : invoke LLM_CLI as the agentic fixer; it fetches the
#                              missing images / fixes malformed data and re-runs
#                              validate.py until it PASSES (or every miss is
#                              acknowledged in media_fallbacks.json).
#   4. final validate        : echo the result; ALWAYS exit 0 (non-fatal).
#
# build.py renders fallback tiles for anything still missing, so the cron is
# never broken regardless of the gate verdict.
set -uo pipefail
# Non-fatal to the cron: any unexpected early termination still exits 0.
trap 'exit 0' EXIT

HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=config.sh
source "$HERE/config.sh"

DAY_DIR="${1:-}"

ENRICH="$HERE/enrich.py"
VALIDATE="$HERE/validate.py"
RESIZE_HELPER="$HERE/resize_thumb.py"

if [[ -z "$DAY_DIR" ]]; then
  echo "[enrich_gate] usage: enrich_gate.sh <day-dir> (skipped)"
  exit 0
fi
if [[ ! -d "$DAY_DIR" ]]; then
  echo "[enrich_gate] day-dir not found: $DAY_DIR (skipped, non-fatal)"
  exit 0
fi
# Canonicalize to an ABSOLUTE path. Critical: codex runs with workdir=$SCRATCH
# (under /tmp) and `--add-dir "$DAY_DIR"`; if DAY_DIR is RELATIVE, codex tries to
# bind-mount it UNDER the scratch workdir and bwrap fails ("Can't bind mount ...
# /newroot/tmp/<scratch>/daily ... No such file or directory"), so the fixer can
# never read the data. An absolute path mounts correctly (same as deepen.sh,
# which always receives run.sh's absolute $ROOT/$DATE). Also keeps enrich/validate
# consistent regardless of the caller's cwd.
DAY_DIR="$(cd "$DAY_DIR" && pwd)"
if [[ ! -s "$DAY_DIR/05-deep.md" ]]; then
  echo "[enrich_gate] $DAY_DIR/05-deep.md missing/empty; nothing to gate (skipped)"
  exit 0
fi

DATE_STR="$(basename "$DAY_DIR")"

# --- 1. deterministic enrich ------------------------------------------------
echo "[enrich_gate] step 1: deterministic enrich (enrich.py)"
python3 "$ENRICH" "$DAY_DIR" 2>&1 \
  || echo "[enrich_gate] enrich.py returned nonzero (non-fatal, continuing to gate)"

# --- 2. first gate ----------------------------------------------------------
echo "[enrich_gate] step 2: validate.py"
if python3 "$VALIDATE" "$DAY_DIR"; then
  echo "[enrich_gate] status: PASS after deterministic enrich (no Codex needed)"
  exit 0
fi

# --- 3. Codex agentic fixer -------------------------------------------------
echo "[enrich_gate] step 3: gate FAILED -> invoking Codex to fetch/fix until PASS"

if ! command -v "$LLM_CLI" >/dev/null 2>&1; then
  echo "[enrich_gate] $LLM_CLI CLI not found; cannot run agentic fixer (non-fatal)"
  echo "[enrich_gate] running final validate for the record:"
  python3 "$VALIDATE" "$DAY_DIR" || true
  echo "[enrich_gate] status: FAIL ($LLM_CLI unavailable); build.py will use fallback tiles"
  exit 0
fi

read -r -d '' PROMPT <<EOF || true
You are fixing the the daily digest card-site data for $DATE_STR so it passes a
strict format-validation gate. The day's directory is: $DAY_DIR

THE GATE (run it, read its FAIL lines, act, repeat):
  python3 $VALIDATE $DAY_DIR
It exits 0 = PASS, 1 = FAIL with one "FAIL: ..." line per problem.

For EACH FAIL line:

- "coverage: card item [^N] has NO image entry ... (url=..., kind=...)":
  Get ONE REAL representative image for that item. You have full network access
  and a working headless Chrome. CRITICAL: never draw, generate, synthesize, or
  fabricate an image — only ever save a genuine capture of the REAL page/asset.
  An invented image in a cited daily is a serious integrity failure.
  Get it in this order of preference:
    * arxiv  -> a real figure from the paper (not a blank/mask).
    * github -> the GitHub social card:
                https://opengraph.githubassets.com/1/<owner>/<repo>
    * blog/web with an og:image / twitter:image -> download that.
    * Hacker News item (news.ycombinator.com/item?id=...) -> the LINKED story is
                the real content, not the comment thread. Open the item, follow
                its external link, and image THAT article (its og:image, else a
                screenshot of it). Only if it is a self / Ask HN / Show HN text
                post with no external link, screenshot the thread page itself.
    * anything else with no og:image -> SCREENSHOT the real page yourself:
        google-chrome-stable --headless=new --no-sandbox --disable-gpu \\
          --hide-scrollbars --disable-dev-shm-usage \\
          --user-agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36' \\
          --window-size=1200,900 --virtual-time-budget=8000 \\
          --screenshot=$DAY_DIR/media/item-N.png '<the-real-url>'
      Then VERIFY the capture is real content, NOT a blank/near-white page or a
      bot-block/rate-limit page (e.g. Hacker News serving just "Sorry."). Check it
      (file type, size, and that it is not almost-all-white). If it came back
      blank/blocked, image the underlying article instead, or retry once — do NOT
      keep a blank/error screenshot, and do NOT fabricate a replacement.
  HARD RULES on the saved file (this is exactly where fabrication sneaks in):
    - It MUST be a real RASTER: png | jpg | jpeg | webp. NEVER .svg for web/blog/
      HN items — chrome screenshots are PNG and real og:images are rasters; an
      .svg here means you hand-built it.
    - It MUST be obtained by EXACTLY ONE of: (a) curl-downloading a real
      og:image/twitter:image asset URL, or (b) the chrome --screenshot command
      above. Nothing else is acceptable.
    - You MUST NOT write, author, compose, draw, or generate the image yourself.
      Writing SVG/HTML markup, or building a "card" from the title/text, IS
      fabrication and is strictly forbidden — it poisons a cited publication.
    - After saving each image, state the exact command you used to obtain it (the
      curl asset URL, or the chrome line) so it is auditable.
  Save to $DAY_DIR/media/item-N.<ext> (ext = png|jpg|jpeg|webp) and
  downscale rasters to max width 720:
    python3 $RESIZE_HELPER $DAY_DIR/media/item-N.<ext> 720
  Then add/update its entry in $DAY_DIR/media.json. IMPORTANT: the img path is
  DATE-NAMESPACED — use the day's date $DATE_STR exactly:
    {"N": {"img": "assets/thumbs/$DATE_STR/item-N.<ext>", "kind": "<arxiv|github|web>"}}
  (media.json is a JSON object mapping numeric-string keys to {img,kind}.)
  Acknowledging "no image" is a LAST RESORT, not a shortcut: only after you have
  genuinely tried the og:image AND a real screenshot and the page is truly
  dead/404/paywalled/blocked, record it in $DAY_DIR/media_fallbacks.json as
  {"N": "short reason"} (a JSON object). Prefer a real screenshot every time.

- "media.json: entry 'N' img '...' does not resolve ...": the file is
  missing/corrupt/too-small. Re-fetch a good image (save to
  $DAY_DIR/media/item-N.<ext>, resize, keep the same date-namespaced img path),
  or if unobtainable remove that entry from media.json and add an acknowledgement
  in media_fallbacks.json.

- "media.json: ..." structural problems (bad JSON / non-numeric key / missing
  img / invalid kind): fix media.json so keys are numeric strings and each value
  is {"img": "<assets/thumbs/$DATE_STR/item-N.ext>", "kind": "arxiv|github|web"}.

- "05-deep.md: ..." structural problems (missing header / unparseable Metadata
  line / inline [^N] with no References entry / reference with no URL): fix the
  structural issue in $DAY_DIR/05-deep.md (do not invent content — fix headers,
  the Metadata line format, or add the missing References URL).

After EACH fix pass, RUN python3 $VALIDATE $DAY_DIR again and keep going until it
exits 0 (PASS). Do not stop until it PASSES or you have genuinely exhausted all
real options for every remaining item (in which case acknowledge them in
media_fallbacks.json so the gate passes). Make all edits inside $DAY_DIR only.
EOF

# Ephemeral scratch workspace (codex session metadata lands in /tmp, not the
# user-visible day-dir). Same pattern as deepen.sh. Bound to 900s.
#
# SANDBOX: -s danger-full-access (NOT workspace-write). The image work REQUIRES
# network + a working headless Chrome, and codex's workspace-write sandbox blocks
# BOTH (no DNS/network; Chrome dies with setsockopt/crashpad). Under workspace-write
# the fixer could never fetch or screenshot anything — it would only acknowledge
# fallbacks, or worse FABRICATE an image from memory. danger-full-access lets it
# fetch og images and run real Chrome screenshots of real pages. Trade-off: codex
# runs model-generated commands unsandboxed in the daily cron, which widens the
# prompt-injection surface from fetched web content; the prompt is scoped to
# imaging this day-dir and explicitly forbids fabricating images.
SCRATCH=$(mktemp -d /tmp/feed-enrich-gate.XXXXXX)
echo "[enrich_gate] running: timeout 900 $LLM_CLI exec -s danger-full-access (scratch=$SCRATCH)"
timeout 900 "$LLM_CLI" exec \
  --skip-git-repo-check \
  --ephemeral \
  -s danger-full-access \
  --add-dir "$DAY_DIR" \
  -C "$SCRATCH" \
  --output-last-message "$SCRATCH/.last" \
  "$PROMPT" 2>&1 \
  || echo "[enrich_gate] codex exited nonzero / timed out (non-fatal)"
rm -rf "$SCRATCH"

# --- 4. final gate ----------------------------------------------------------
echo "[enrich_gate] step 4: final validate.py"
if python3 "$VALIDATE" "$DAY_DIR"; then
  echo "[enrich_gate] status: PASS after Codex fixer"
else
  echo "[enrich_gate] status: FAIL after Codex fixer (build.py will render fallback tiles; cron unaffected)"
fi

exit 0
