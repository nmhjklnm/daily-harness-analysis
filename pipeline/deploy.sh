#!/usr/bin/env bash
# Host-agnostic site deployer driven by DEPLOY_TARGET.
#
# Supported targets: none | vercel | netlify | rsync | command
#
# Set DEPLOY_TARGET (and the corresponding credentials) in .env or environment.
# See .env.example for all variables.
#
# NON-FATAL: trap converts any unexpected exit into exit 0; never blocks cron.
set -uo pipefail
trap 'exit 0' EXIT

HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=config.sh
source "$HERE/config.sh"

: "${DEPLOY_TARGET:=none}"

export VERCEL_TELEMETRY_DISABLED=1

if [[ ! -d "$SITE_DIR" ]]; then
  echo "deploy: SITE_DIR not found: $SITE_DIR" >&2
  exit 0
fi

cd "$SITE_DIR" || { echo "deploy: cannot cd to $SITE_DIR" >&2; exit 0; }

case "$DEPLOY_TARGET" in
  none)
    echo "deploy: DEPLOY_TARGET=none, skipping"
    exit 0
    ;;

  vercel)
    if [[ -z "${VERCEL_TOKEN:-}" ]]; then
      echo "deploy: VERCEL_TOKEN is not set — see .env.example" >&2
      exit 0
    fi
    if ! command -v vercel >/dev/null 2>&1; then
      echo "deploy: vercel CLI not found (npm i -g vercel)" >&2
      exit 0
    fi
    # Link to project first if VERCEL_PROJECT is specified.
    if [[ -n "${VERCEL_PROJECT:-}" ]]; then
      vercel link --yes \
        --project "$VERCEL_PROJECT" \
        ${VERCEL_SCOPE:+--scope "$VERCEL_SCOPE"} \
        --token "$VERCEL_TOKEN"
    fi
    echo "deploy: deploying to Vercel production..."
    vercel deploy --prod --yes \
      --token "$VERCEL_TOKEN" \
      ${VERCEL_SCOPE:+--scope "$VERCEL_SCOPE"}
    ;;

  netlify)
    if [[ -z "${NETLIFY_AUTH_TOKEN:-}" ]]; then
      echo "deploy: NETLIFY_AUTH_TOKEN is not set — see .env.example" >&2
      exit 0
    fi
    if ! command -v netlify >/dev/null 2>&1; then
      echo "deploy: netlify CLI not found (npm i -g netlify-cli)" >&2
      exit 0
    fi
    echo "deploy: deploying to Netlify production..."
    netlify deploy --prod --dir . \
      --auth "$NETLIFY_AUTH_TOKEN" \
      ${NETLIFY_SITE_ID:+--site "$NETLIFY_SITE_ID"}
    ;;

  rsync)
    if [[ -z "${RSYNC_DEST:-}" ]]; then
      echo "deploy: RSYNC_DEST is not set — see .env.example" >&2
      exit 0
    fi
    echo "deploy: rsync to $RSYNC_DEST ..."
    rsync -az --delete ./ "$RSYNC_DEST"
    ;;

  command)
    if [[ -z "${DEPLOY_COMMAND:-}" ]]; then
      echo "deploy: DEPLOY_COMMAND is not set — see .env.example" >&2
      exit 0
    fi
    echo "deploy: running DEPLOY_COMMAND: $DEPLOY_COMMAND"
    eval "$DEPLOY_COMMAND"
    ;;

  *)
    echo "deploy: unknown DEPLOY_TARGET='$DEPLOY_TARGET' (expected none|vercel|netlify|rsync|command)" >&2
    exit 0
    ;;
esac
