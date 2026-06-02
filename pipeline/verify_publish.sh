#!/usr/bin/env bash
# Verify the daily card-site issue for <YYYY-MM-DD> is actually LIVE.
# Used by run.sh after deploy to detect silent publish failures:
# deploy.sh always exits 0 (its trap), so its exit code is not a usable
# signal. The only trustworthy signal is the live site itself.
#
# Usage: verify_publish.sh <YYYY-MM-DD>
#   Exit 0  -> issue page is live (HTTP 200, non-trivial body) AND linked from
#              the index (so it shows up as the latest card).
#   Exit 1  -> not live after polling.
#   Exit 2  -> bad usage.
#
# Tunables (env): DAILY_SITE_URL, VERIFY_TRIES, VERIFY_SLEEP, VERIFY_MIN_BYTES
set -uo pipefail

HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=config.sh
source "$HERE/config.sh"

DATE="${1:-}"
SITE="${DAILY_SITE_URL:-$SITE_DOMAIN}"
TRIES="${VERIFY_TRIES:-6}"        # poll up to TRIES times (Vercel propagation)
SLEEP="${VERIFY_SLEEP:-10}"       # seconds between polls
MIN_BYTES="${VERIFY_MIN_BYTES:-5000}"  # a real issue page is ~110KB

if [[ -z "$DATE" ]]; then
  echo "[verify_publish] usage: verify_publish.sh <YYYY-MM-DD>" >&2
  exit 2
fi

ISSUE_URL="$SITE/$DATE.html"

for ((i=1; i<=TRIES; i++)); do
  nonce="$(date -u +%s)$i"   # cache-bust so we read fresh origin, not stale CDN
  body="$(curl -fsSL --max-time 20 "$ISSUE_URL?t=$nonce" 2>/dev/null)"; rc=$?
  bytes=${#body}
  idx="$(curl -fsSL --max-time 20 "$SITE/?t=$nonce" 2>/dev/null)"
  linked=n; grep -q "$DATE.html" <<<"$idx" && linked=y

  if [[ $rc -eq 0 && $bytes -ge $MIN_BYTES && $linked == y ]]; then
    echo "[verify_publish] LIVE: $ISSUE_URL (${bytes}B), linked from index (try $i/$TRIES)"
    exit 0
  fi
  echo "[verify_publish] not live yet (try $i/$TRIES): issue_rc=$rc bytes=$bytes index_linked=$linked"
  [[ $i -lt $TRIES ]] && sleep "$SLEEP"
done

echo "[verify_publish] FAILED: $DATE not confirmed live after $TRIES tries ($ISSUE_URL)" >&2
exit 1
