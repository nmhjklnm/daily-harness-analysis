#!/usr/bin/env bash
# Failure alert for the daily publish step. Called by run.sh ONLY when
# verify_publish.sh could not confirm the issue went live after auto-retries.
#
# Channels (all best-effort, never fatal — this script always exits 0):
#   1. loud log banner + status file            (always)
#   2. generic webhook  -> set ALERT_WEBHOOK to a URL; MSG is POSTed as the body.
#      Point it at ntfy / bark / a Telegram-bot relay / WeCom robot, etc.
#         e.g. ALERT_WEBHOOK="https://ntfy.sh/<your-topic>"
#   3. email -> set SMTP_ENV_FILE to a file containing SMTP_PASS (and optionally
#      SMTP_FROM, SMTP_HOST, SMTP_PORT). Triggered only if ALERT_EMAIL is also set.
#
# Usage: notify_fail.sh <YYYY-MM-DD> [reason]
set -uo pipefail
trap 'exit 0' EXIT   # never block the caller / cron

HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=config.sh
source "$HERE/config.sh"

DATE="${1:-?}"
REASON="${2:-未能确认上线}"
STATUS_FILE="${PUBLISH_STATUS_FILE:-$OUTPUT_DIR/PUBLISH-STATUS.txt}"
SITE="${DAILY_SITE_URL:-$SITE_DOMAIN}"
TS="$(date -u +%FT%TZ)"
MSG="[$SITE_BRAND] 发布失败: ${DATE} 期 ${REASON} ($SITE)。cron 自动重试后仍未上线，需人工介入。@${TS}"

# 1) loud banner + status file (always) ----------------------------------------
echo "########################################################################"
echo "## PUBLISH ALERT  $MSG"
echo "########################################################################"
printf 'PUBLISH FAILED %s %s\n%s\n' "$DATE" "$TS" "$MSG" > "$STATUS_FILE" 2>/dev/null || true

# 2) generic webhook (opt-in via env) ------------------------------------------
if [[ -n "${ALERT_WEBHOOK:-}" ]]; then
  curl -fsS --max-time 15 -d "$MSG" "$ALERT_WEBHOOK" >/dev/null 2>&1 \
    && echo "[notify_fail] webhook alert sent" \
    || echo "[notify_fail] webhook alert FAILED (non-fatal)"
fi

# 3) email (only if SMTP_ENV_FILE points to a credentials file with SMTP_PASS) -
# The SMTP env file may contain: SMTP_PASS, SMTP_FROM, SMTP_HOST, SMTP_PORT.
# SMTP_FROM defaults to ALERT_EMAIL; SMTP_HOST defaults to localhost; SMTP_PORT to 587.
SMTP_ENV="${SMTP_ENV_FILE:-}"
ALERT_EMAIL="${ALERT_EMAIL:-}"
if [[ -n "$ALERT_EMAIL" && -n "$SMTP_ENV" && -f "$SMTP_ENV" ]]; then
  SMTP_PASS="$(grep -oP 'SMTP_PASS=\K.*' "$SMTP_ENV" 2>/dev/null || true)"
  SMTP_FROM="$(grep -oP 'SMTP_FROM=\K.*' "$SMTP_ENV" 2>/dev/null || true)"
  SMTP_HOST="$(grep -oP 'SMTP_HOST=\K.*' "$SMTP_ENV" 2>/dev/null || true)"
  SMTP_PORT="$(grep -oP 'SMTP_PORT=\K.*' "$SMTP_ENV" 2>/dev/null || true)"
  if [[ -n "$SMTP_PASS" ]]; then
    SMTP_PASS="$SMTP_PASS" SMTP_FROM="${SMTP_FROM:-$ALERT_EMAIL}" \
    SMTP_HOST="${SMTP_HOST:-localhost}" SMTP_PORT="${SMTP_PORT:-587}" \
    ALERT_EMAIL="$ALERT_EMAIL" MSG="$MSG" DATE="$DATE" \
    python3 - <<'PY' && echo "[notify_fail] email alert sent" || echo "[notify_fail] email alert FAILED (non-fatal)"
import os, smtplib
from email.message import EmailMessage
m = EmailMessage()
smtp_from = os.environ["SMTP_FROM"]
m["From"] = smtp_from
m["To"] = os.environ["ALERT_EMAIL"]
m["Subject"] = f"[{os.environ.get('SITE_BRAND','Daily Harness')}] 发布失败 {os.environ['DATE']}"
m.set_content(os.environ["MSG"])
s = smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ["SMTP_PORT"]), timeout=20)
s.starttls(); s.login(smtp_from, os.environ["SMTP_PASS"])
s.send_message(m); s.quit()
PY
  fi
fi
