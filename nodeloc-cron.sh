#!/usr/bin/env bash
set -euo pipefail

export DISPLAY=:1
export XDG_RUNTIME_DIR=/tmp/runtime-root
export XAUTHORITY=/root/.Xauthority
export DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS:-}
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

OUT=$(python3 /root/.openclaw/skills/nodeseek-signin/scripts/nodeloc_signin.py 2>&1 || true)
MSG=$(printf '%s' "$OUT" | python3 /root/.openclaw/scripts/notify-signin.py nodeloc)
curl -s -X POST "https://api.telegram.org/bot7782089550:AAEUqhMiqcPdZJ4vzhfWGLGG3yBuQyl-Lgs/sendMessage" \
  -d chat_id="7387265533" \
  --data-urlencode text="$MSG" >/dev/null
printf '%s\n' "$MSG"
