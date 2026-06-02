#!/usr/bin/env bash
# Install the daily-harness pipeline.
# Symlinks run.sh into the target bin dir; optionally adds a cron entry.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=config.sh
source "$HERE/config.sh"

TARGET="${DHA_BIN:-$HOME/.local/bin/daily-harness}"
LOG="${FEED_BRIEF_LOG:-$DHA_ROOT/daily-harness.log}"
CRON_LINE="0 23 * * * $TARGET >> $LOG 2>&1"   # UTC 23:00 = Asia/Shanghai 07:00 next day

# 1. Make scripts executable
chmod +x "$HERE/run.sh" "$HERE/daily_brief.py" "$HERE/deepen.sh" "$HERE/md_to_html.py"
echo "chmod +x scripts"

# 2. Symlink run.sh as entry
mkdir -p "$(dirname "$TARGET")"
ln -sf "$HERE/run.sh" "$TARGET"
echo "symlinked: $TARGET -> $HERE/run.sh"

# 3. Log file
touch "$LOG"
echo "log: $LOG"

# 4. Sanity checks
command -v "$LLM_CLI" >/dev/null || {
  echo "WARN: $LLM_CLI (LLM_CLI) not found"
  echo "  Install: npm i -g @openai/codex   (or set LLM_CLI to another compatible CLI)"
  echo "  Auth   : run \`$LLM_CLI login\` or set OPENAI_API_KEY in .env"
}
command -v ssh    >/dev/null || { echo "WARN: ssh not found"; exit 1; }
command -v python3 >/dev/null || { echo "WARN: python3 not found"; exit 1; }
command -v uv >/dev/null || [ -x "$HOME/.local/bin/uv" ] || {
  echo "WARN: uv not found (HTML/PDF render needs it; install: curl -LsSf https://astral.sh/uv/install.sh | sh)"
}

# Only check SSH connectivity when MINIFLUX_SSH_HOST is configured.
if [[ -n "${MINIFLUX_SSH_HOST:-}" ]]; then
  ssh -o ConnectTimeout=10 -o BatchMode=yes "$MINIFLUX_SSH_HOST" true 2>/dev/null \
    || echo "WARN: ssh $MINIFLUX_SSH_HOST failed (BatchMode); verify SSH access before running cron"
fi

# Note: verify LLM_CLI auth separately (e.g. `codex login status`).
echo "NOTE: verify $LLM_CLI auth before first run (e.g. \`$LLM_CLI login status\`)"

# 5. Cron (idempotent)
if crontab -l 2>/dev/null | grep -qF "$TARGET"; then
  echo "cron: already installed"
else
  (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
  echo "cron: added '$CRON_LINE'"
fi

echo
echo "OK. 手跑一次:"
echo "  $TARGET                       # full pipeline"
echo "  $TARGET --shallow-only        # 仅浅日报 (~3 min)"
echo
echo "卸载:"
echo "  crontab -l | grep -v daily-harness | crontab -"
echo "  rm $TARGET"
