#!/usr/bin/env bash
set -euo pipefail

export DISPLAY=:1
export XDG_RUNTIME_DIR=/tmp/runtime-root
export XAUTHORITY=/root/.Xauthority
export DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS:-}
export PATH=/usr/local/node/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export OPENCLAW_BIN=/usr/local/node/bin/openclaw
export CHROME_BIN=/usr/bin/google-chrome
export HOHAI_KEEP_OPEN_ON_FAIL=0

OUT=$(HOME=/root /root/.openclaw/venvs/seleniumbase/bin/python /root/.openclaw/scripts/hohai-sb.py 2>&1 || true)
MSG=$(printf '%s' "$OUT" | python3 /root/.openclaw/scripts/notify-signin.py hohai)
curl -s -X POST "https://api.telegram.org/bot7782089550:AAEUqhMiqcPdZJ4vzhfWGLGG3yBuQyl-Lgs/sendMessage" \
  -d chat_id="7387265533" \
  --data-urlencode text="$MSG" >/dev/null
printf '%s\n' "$MSG"
