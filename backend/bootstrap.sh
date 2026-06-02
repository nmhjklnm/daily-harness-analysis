#!/usr/bin/env bash
# bootstrap.sh — spin up the feed backend and mint a Miniflux API token.
# Run from the backend/ directory after copying and editing .env.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"

# ── Load .env ─────────────────────────────────────────────────────────────────
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "ERROR: ${ENV_FILE} not found. Copy .env.example to .env and edit it first." >&2
  exit 1
fi

# Export vars from .env, skipping comments and blank lines.
set -o allexport
# shellcheck source=/dev/null
source "${ENV_FILE}"
set +o allexport

: "${MINIFLUX_ADMIN_USERNAME:?MINIFLUX_ADMIN_USERNAME is not set in .env}"
: "${MINIFLUX_ADMIN_PASSWORD:?MINIFLUX_ADMIN_PASSWORD is not set in .env}"

MINIFLUX_URL="http://localhost:8080"
OPML_FILE="${SCRIPT_DIR}/feeds.opml.example"

# ── Start services (no proxy profile) ─────────────────────────────────────────
echo "==> Starting db, miniflux, rsshub, redis …"
docker compose -f "${SCRIPT_DIR}/docker-compose.yml" up -d db miniflux rsshub redis

# ── Wait for Miniflux ─────────────────────────────────────────────────────────
echo "==> Waiting for Miniflux to be ready (up to 90 s) …"
TIMEOUT=90
ELAPSED=0
until curl -sf -u "${MINIFLUX_ADMIN_USERNAME}:${MINIFLUX_ADMIN_PASSWORD}" \
      "${MINIFLUX_URL}/v1/me" > /dev/null 2>&1; do
  if (( ELAPSED >= TIMEOUT )); then
    echo "ERROR: Miniflux did not become ready within ${TIMEOUT}s." >&2
    echo "  Check logs: docker compose -f ${SCRIPT_DIR}/docker-compose.yml logs miniflux" >&2
    exit 1
  fi
  sleep 3
  ELAPSED=$(( ELAPSED + 3 ))
done
echo "  Miniflux is up."

# ── Mint API token ─────────────────────────────────────────────────────────────
echo "==> Minting API token …"
TOKEN_DESC="daily-harness pipeline"
RESPONSE=$(curl -sf \
  -u "${MINIFLUX_ADMIN_USERNAME}:${MINIFLUX_ADMIN_PASSWORD}" \
  -X POST "${MINIFLUX_URL}/v1/api-keys" \
  -H "Content-Type: application/json" \
  -d "{\"description\":\"${TOKEN_DESC}\"}" 2>&1) || true

# Miniflux returns an error if a key with the same description already exists.
if echo "${RESPONSE}" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if 'token' in d else 1)" 2>/dev/null; then
  API_TOKEN=$(echo "${RESPONSE}" | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
else
  echo ""
  echo "WARNING: Could not create a new API key with description \"${TOKEN_DESC}\"."
  echo "  This usually means a key with that description already exists."
  echo "  To list existing keys:"
  echo "    curl -s -u \"\$MINIFLUX_ADMIN_USERNAME:\$MINIFLUX_ADMIN_PASSWORD\" ${MINIFLUX_URL}/v1/api-keys"
  echo "  Then copy the existing token value and set MINIFLUX_TOKEN manually."
  echo ""
  echo "  Raw response: ${RESPONSE}"
  API_TOKEN=""
fi

# ── Import starter feeds ───────────────────────────────────────────────────────
if [[ -f "${OPML_FILE}" ]]; then
  echo "==> Importing starter feeds from feeds.opml.example …"
  IMPORT_RESP=$(curl -sf \
    -u "${MINIFLUX_ADMIN_USERNAME}:${MINIFLUX_ADMIN_PASSWORD}" \
    -X POST \
    -F "file=@${OPML_FILE}" \
    "${MINIFLUX_URL}/v1/import" 2>&1) || true
  echo "  Import response: ${IMPORT_RESP}"
else
  echo "  feeds.opml.example not found — skipping feed import."
fi

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════"
echo " Feed backend is running."
echo " Web UI:   ${MINIFLUX_URL}   (login: ${MINIFLUX_ADMIN_USERNAME})"
if [[ -n "${API_TOKEN}" ]]; then
  echo ""
  echo " API token minted. Add this to your repo-root .env:"
  echo ""
  echo "   MINIFLUX_TOKEN=${API_TOKEN}"
  echo ""
  echo " (The token is shown once — store it now.)"
fi
echo "════════════════════════════════════════════════════"
