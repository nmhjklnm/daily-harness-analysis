#!/usr/bin/env bash
# Daily feed brief pipeline orchestrator.
#
# Layered output to feeds/daily/YYYY-MM-DD/:
#   01-raw.json       raw 24h entries from Miniflux
#   03-digest.md      shallow digest (codex, 1 line per entry)
#   03-digest.html    digest HTML
#   03-digest.pdf     digest PDF
#   04-sources/       cached fetched content per URL (codex agentic)
#   05-deep.md        deep brief with [^N] inline footnotes (codex agentic)
#   06-deep.html      deep brief HTML
#   07-deep.pdf       deep brief PDF
#
# Usage:
#   ./run.sh                        # default: today, full pipeline
#   ./run.sh --date 2026-05-28      # specific day
#   ./run.sh --shallow-only         # skip deepen stage (fast, ~3 min)
#   ./run.sh --hours 48             # look back 48h
set -uo pipefail

HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=config.sh
source "$HERE/config.sh"

ROOT="$OUTPUT_DIR"
LOG="${FEED_BRIEF_LOG:-$DHA_ROOT/daily-harness.log}"

DATE="$(TZ=Asia/Shanghai date +%F)"   # Beijing date: folder name + deep title (= 北京早晨投递日)
HOURS=24
SHALLOW_ONLY=0
SCP_TO=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --date) DATE="$2"; shift 2 ;;
    --hours) HOURS="$2"; shift 2 ;;
    --shallow-only) SHALLOW_ONLY=1; shift ;;
    --scp-to) SCP_TO="$2"; shift 2 ;;
    *) echo "unknown arg: $1"; exit 2 ;;
  esac
done

export PATH="$PATH:$HOME/.local/bin"
DAY_DIR="$ROOT/$DATE"
mkdir -p "$DAY_DIR/04-sources"

ts() { date -u +%FT%TZ; }
log() { echo "[$(ts)] $*" | tee -a "$LOG" >&2; }

log "=== pipeline start, day-dir=$DAY_DIR, hours=$HOURS, shallow_only=$SHALLOW_ONLY ==="

# Stage 1-3: shallow brief (raw → digest md/html/pdf)
log "stage shallow: calling daily_brief.py"
"$HERE/daily_brief.py" --hours "$HOURS" --day-dir "$DAY_DIR" 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
if [[ $rc -ne 0 ]]; then
  log "shallow failed with $rc"
  exit $rc
fi

if [[ $SHALLOW_ONLY -eq 1 ]]; then
  log "shallow-only; skipping deepen"
  exit 0
fi

# Stage 4-5: deepen (codex agentic per-URL read)
log "stage deepen: calling deepen.sh"
"$HERE/deepen.sh" "$DAY_DIR" 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
if [[ $rc -ne 0 ]]; then
  log "deepen failed with $rc"
  exit $rc
fi

# Stage 6-7: render deep html + pdf
log "stage render: deep md → html + pdf"
"$HERE/md_to_html.py" \
  "$DAY_DIR/05-deep.md" \
  "$DAY_DIR/06-deep.html" \
  --pdf "$DAY_DIR/07-deep.pdf" 2>&1 | tee -a "$LOG"

# Optional: scp to remote (e.g. mbp)
if [[ -n "$SCP_TO" ]]; then
  log "scp md/html/pdf to $SCP_TO"
  scp "$DAY_DIR/05-deep.md" "$DAY_DIR/06-deep.html" "$DAY_DIR/07-deep.pdf" "$SCP_TO" 2>&1 | tee -a "$LOG"
fi

log "=== verifying all layers present ==="
missing=()
for f in 01-raw.json 03-digest.md 03-digest.html 03-digest.pdf; do
  [[ -s "$DAY_DIR/$f" ]] || missing+=("$f")
done
if [[ $SHALLOW_ONLY -eq 0 ]]; then
  for f in 02-filtered.json 05-deep.md 06-deep.html 07-deep.pdf; do
    [[ -s "$DAY_DIR/$f" ]] || missing+=("$f")
  done
  src_count=$(ls "$DAY_DIR/04-sources/" 2>/dev/null | wc -l)
  [[ $src_count -gt 0 ]] || missing+=("04-sources/*")
fi
if [[ ${#missing[@]} -gt 0 ]]; then
  log "MISSING LAYERS: ${missing[*]}"
  exit 3
fi

# --- ADDITIVE, NON-FATAL: card-site render -----------------------------------
# Runs ONLY after the proven md/html/pdf layers above are produced + verified.
# Never gates or alters those artifacts; any failure is logged and swallowed.
echo "=== render card site (additive, non-fatal) ==="
bash "$HERE/render_site.sh" "$DAY_DIR" "$DATE" 2>&1 | tee -a "$LOG" \
  || echo "[render_site] non-fatal failure, daily brief unaffected" | tee -a "$LOG"

# --- ADDITIVE, NON-FATAL: publish to Vercel, with verify + auto-retry ---------
# deploy_vercel.sh always exits 0 (its trap), so its exit code is NOT a usable
# signal. Instead we VERIFY the issue is actually live (verify_publish.sh polls
# the real site) and RE-DEPLOY up to PUBLISH_ATTEMPTS times to ride out transient
# Vercel / network / propagation failures. Only a persistent failure (the issue
# still isn't live after all retries) pings notify_fail.sh. Still fully additive:
# the proven md/html/pdf brief above is never affected by anything here.
publish_ok=0
for attempt in $(seq 1 "$PUBLISH_ATTEMPTS"); do
  echo "=== deploy (attempt $attempt/$PUBLISH_ATTEMPTS, additive, non-fatal) ===" | tee -a "$LOG"
  bash "$HERE/deploy.sh" 2>&1 | tee -a "$LOG" \
    || echo "[deploy] non-fatal failure, daily brief unaffected" | tee -a "$LOG"
  # pipefail is set above, so the if-condition reflects verify_publish's exit
  # code (not tee's): exit 0 => issue confirmed live; nonzero => not live yet.
  if bash "$HERE/verify_publish.sh" "$DATE" 2>&1 | tee -a "$LOG"; then
    publish_ok=1
    break
  fi
  echo "[publish] attempt $attempt did not verify live; retrying after backoff" | tee -a "$LOG"
  [[ "$attempt" -lt "$PUBLISH_ATTEMPTS" ]] && sleep 20
done

if [[ "$publish_ok" -eq 1 ]]; then
  printf 'PUBLISH OK %s %s\n' "$DATE" "$(ts)" > "$OUTPUT_DIR/PUBLISH-STATUS.txt" 2>/dev/null || true
  log "[publish] verified live: $DATE"
else
  log "[publish] FAILED to verify $DATE live after $PUBLISH_ATTEMPTS attempts -> alerting"
  bash "$HERE/notify_fail.sh" "$DATE" "重试 ${PUBLISH_ATTEMPTS} 次后仍未上线" 2>&1 | tee -a "$LOG" || true
fi

log "=== pipeline done ==="
ls -la "$DAY_DIR/" | tee -a "$LOG"
