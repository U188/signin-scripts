#!/usr/bin/env bash
# HOHAI 定时签到包装脚本（CST 08:00 由 cron 调用）
set -uo pipefail

ENV_FILE="${HOHAI_ENV_FILE:-/root/.config/hohai-signin.env}"
SCRIPT="${HOHAI_SCRIPT:-/root/Desktop/signin-scripts/hohai-sb.py}"
LOG_DIR="${HOHAI_LOG_DIR:-/root/.local/share/hohai-signin/logs}"
LOCK_FILE="${HOHAI_LOCK_FILE:-/tmp/hohai-signin.lock}"
mkdir -p "$LOG_DIR"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $*" | tee -a "$LOG_DIR/cron.log"; }

if [[ ! -f "$ENV_FILE" ]]; then
  log "FATAL: missing env file $ENV_FILE"
  exit 2
fi
if [[ ! -x "$SCRIPT" && ! -f "$SCRIPT" ]]; then
  log "FATAL: missing script $SCRIPT"
  exit 2
fi

# 单实例锁
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "SKIP: another hohai-signin is running"
  exit 0
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

export DISPLAY="${DISPLAY:-:1}"
export HOME="${HOME:-/root}"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

# 确保 X/VNC :1 可用；没有则尽量拉起
if [[ ! -S /tmp/.X11-unix/X1 ]]; then
  log "WARN: X1 socket missing, try start vncserver :1"
  if command -v vncserver >/dev/null 2>&1; then
    vncserver :1 -geometry 1920x1080 -depth 24 >/tmp/hohai-vnc-start.log 2>&1 || true
    sleep 2
  fi
fi
if [[ ! -S /tmp/.X11-unix/X1 ]]; then
  log "FATAL: DISPLAY :1 not available"
  exit 3
fi

STAMP="$(date '+%Y%m%d_%H%M%S')"
RUN_LOG="$LOG_DIR/run_${STAMP}.log"
log "START proxy=${HOHAI_PROXY:-direct} user=${HOHAI_USERNAME:-?} script=$SCRIPT"

# 跑签到
set +e
/root/.openclaw/venvs/seleniumbase/bin/python "$SCRIPT" >>"$RUN_LOG" 2>&1
rc=$?
set -e

# 摘要进 cron.log
tail -n 40 "$RUN_LOG" | while IFS= read -r line; do log "OUT $line"; done
log "END rc=$rc log=$RUN_LOG"

# 保留最近 30 份 run log
ls -1t "$LOG_DIR"/run_*.log 2>/dev/null | tail -n +31 | xargs -r rm -f

exit "$rc"
