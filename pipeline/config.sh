#!/usr/bin/env bash
# Shared config for the pipeline. Source this at the top of every shell script:
#   source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/config.sh"
#
# It locates the repo root, loads `.env` (if present) without clobbering vars
# already set in the environment, and derives default paths. Nothing here is
# machine-specific — everything resolves relative to the repo or to .env.

# Repo root = parent of the pipeline/ dir that holds this file.
DHA_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
export DHA_ROOT

# Load .env: export each KEY=VALUE, but let an already-exported env var win
# (so `MINIFLUX_TOKEN=… ./run.sh` and CI overrides still work).
if [[ -f "$DHA_ROOT/.env" ]]; then
  while IFS= read -r line; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" =~ ^[[:space:]]*$ ]] && continue
    key="${line%%=*}"
    key="${key// /}"
    [[ -z "$key" ]] && continue
    if [[ -z "${!key+x}" ]]; then
      export "$key=${line#*=}"
    fi
  done < "$DHA_ROOT/.env"
fi

# Derived defaults (only set if unset).
: "${OUTPUT_DIR:=$DHA_ROOT/daily}"
: "${SITE_DIR:=$DHA_ROOT/site}"
: "${LOOKBACK_HOURS:=24}"
: "${LLM_CLI:=codex}"
: "${SITE_BRAND:=Daily Harness Analysis}"
: "${SITE_DOMAIN:=http://localhost:8000}"
: "${SITE_REPO_URL:=https://github.com/nmhjklnm/daily-harness-analysis}"
: "${PUBLISH_ATTEMPTS:=3}"
export OUTPUT_DIR SITE_DIR LOOKBACK_HOURS LLM_CLI SITE_BRAND SITE_DOMAIN SITE_REPO_URL PUBLISH_ATTEMPTS
