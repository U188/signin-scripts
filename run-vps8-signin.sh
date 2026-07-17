#!/usr/bin/env bash
# VPS8 自动签到包装：env + DISPLAY + 单实例锁 + 日志
set -euo pipefail

ENV_FILE="${VPS8_ENV_FILE:-/root/.config/vps8-signin.env}"
SCRIPT="${VPS8_SCRIPT:-/root/Desktop/signin-scripts/vps8-signin.py}"
PY="${VPS8_PYTHON:-/root/.openclaw/venvs/seleniumbase/bin/python}"
LOG_DIR="${VPS8_LOG_DIR:-$HOME/.local/share/vps8-signin/logs}"
LOCK_FILE="${VPS8_LOCK_FILE:-/tmp/vps8-signin.lock}"
DISPLAY_NUM="${DISPLAY:-:1}"

mkdir -p "$LOG_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
RUN_LOG="$LOG_DIR/run_${STAMP}.log"
CRON_LOG="$LOG_DIR/cron.log"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[$(date '+%F %T')] another vps8-signin is running" | tee -a "$CRON_LOG"
  exit 0
fi

# shellcheck disable=SC1090
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ENV_FILE"
  set +a
fi

export DISPLAY="$DISPLAY_NUM"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/runtime-root}"
mkdir -p "$XDG_RUNTIME_DIR"

if [[ ! -S /tmp/.X11-unix/X${DISPLAY_NUM#:} ]]; then
  echo "[$(date '+%F %T')] WARN: X display $DISPLAY_NUM socket missing" | tee -a "$CRON_LOG" "$RUN_LOG"
fi

{
  echo "==== VPS8 signin $STAMP ===="
  echo "DISPLAY=$DISPLAY SCRIPT=$SCRIPT"
  "$PY" "$SCRIPT"
  ec=$?
  echo "==== exit $ec ===="
  exit "$ec"
} 2>&1 | tee -a "$RUN_LOG" | tee -a "$CRON_LOG"

# keep last 30 run logs
ls -1t "$LOG_DIR"/run_*.log 2>/dev/null | tail -n +31 | xargs -r rm -f
