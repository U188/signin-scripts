# signin-scripts

三个站点的自动签到脚本：HOHAI、NodeLoc、NodeSeek。

> 说明：仓库中的脚本已脱敏，账号、密码和 Telegram Bot 信息均通过环境变量传入。

## 文件

| 文件 | 站点 | 运行时 |
|---|---|---|
| `hohai-sb.py` | HOHAI | Python + SeleniumBase |
| `run-hohai-signin.sh` | HOHAI 定时包装 | bash（source env + flock + 日志） |
| `nodeloc-signin.py` | NodeLoc | Python + patchright |
| `nodeseek-signin.mjs` | NodeSeek | Node.js + Chrome CDP |

## 环境变量

### 通用 Telegram 通知

```bash
export SIGNIN_TG_BOT_TOKEN='你的 Telegram Bot Token'
export SIGNIN_TG_CHAT_ID='你的 Telegram Chat ID'
```

如果不设置这两个变量，脚本仍会在终端输出结果，但不会发送 Telegram 通知。

`hohai-sb.py` 额外兼容 Hermes 通用变量：`TELEGRAM_BOT_TOKEN`、`TELEGRAM_ALLOWED_USERS`（取第一个 chat id）。

### HOHAI

```bash
export HOHAI_USERNAME='你的 HOHAI 用户名'
export HOHAI_PASSWORD='你的 HOHAI 密码'
export HOHAI_SB_PROFILE='/root/.config/seleniumbase-hohai'

# 可选：SOCKS/HTTP 代理（机房 IP 下 Turnstile 常服务端拒签，建议走可用代理）
# SeleniumBase 格式：user:pass@host:port 或 host:port
# 也接受 socks5://... / http://...（脚本会自动去掉 scheme）
export HOHAI_PROXY='user:pass@host:port'

# 可选：多代理按序尝试（逗号/换行分隔），全失败后再试直连
export HOHAI_PROXY_LIST='user:pass@host1:port,user:pass@host2:port'

# 可选调试
# export HOHAI_HEADED=1
# export HOHAI_KEEP_OPEN_ON_FAIL=0
# export HOHAI_OBSERVE_MODE=0
# export HOHAI_LOGIN_TIMEOUT=50   # 登录页/CF 等待秒数
export DISPLAY=:1
```

运行：

```bash
/root/.openclaw/venvs/seleniumbase/bin/python hohai-sb.py
```

或用包装脚本（推荐定时用）：

```bash
# 私有 env 文件示例（chmod 600，不要提交仓库）
# /root/.config/hohai-signin.env
# HOHAI_USERNAME=...
# HOHAI_PASSWORD=...
# HOHAI_PROXY=user:pass@host:port
# DISPLAY=:1
# SIGNIN_TG_BOT_TOKEN=...
# SIGNIN_TG_CHAT_ID=...

./run-hohai-signin.sh
```

成功判定以 `POST /api/checkin` 响应为准；验证失败会自动重开签到弹窗再试。

### NodeLoc

```bash
export NODELOC_USERNAME='你的 NodeLoc 用户名'
export NODELOC_PASSWORD='你的 NodeLoc 密码'
export CDP_URL='http://127.0.0.1:18800'
```

运行：

```bash
python3 nodeloc-signin.py
```

### NodeSeek

NodeSeek 复用已登录浏览器会话，不在脚本中保存账号密码。

```bash
export OPENCLAW_CDP_HTTP='http://127.0.0.1:18800'
```

运行：

```bash
/usr/local/node/bin/node nodeseek-signin.mjs
```

## 定时示例

### HOHAI（推荐 env 文件 + 包装脚本）

```cron
0 8 * * * /path/to/run-hohai-signin.sh
```

包装脚本会：

1. `source` 私有 env（默认 `/root/.config/hohai-signin.env`）
2. 检查 `DISPLAY=:1` / X11 socket
3. flock 防重入
4. 写日志到 `~/.local/share/hohai-signin/logs/`

若使用 Hermes Agent，也可：

```bash
hermes cron create '0 8 * * *' \
  --name hohai-signin-daily-0800 \
  --script run-hohai-signin.sh \
  --no-agent \
  --deliver local
```

（需把 `run-hohai-signin.sh` 放到 `~/.hermes/scripts/`）

### 通用 crontab（直接传环境变量）

需要 GUI/Chrome 的环境建议显式传入：

```cron
0 8 * * * DISPLAY=:1 HOME=/root PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOHAI_USERNAME='xxx' HOHAI_PASSWORD='xxx' HOHAI_PROXY='user:pass@host:port' SIGNIN_TG_BOT_TOKEN='xxx' SIGNIN_TG_CHAT_ID='xxx' /root/.openclaw/venvs/seleniumbase/bin/python /path/to/hohai-sb.py >> /tmp/hohai-cron.log 2>&1

10 8,20 * * * DISPLAY=:1 HOME=/root PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin NODELOC_USERNAME='xxx' NODELOC_PASSWORD='xxx' SIGNIN_TG_BOT_TOKEN='xxx' SIGNIN_TG_CHAT_ID='xxx' python3 /path/to/nodeloc-signin.py >> /tmp/nodeloc-cron.log 2>&1

50 8,20 * * * HOME=/root PATH=/usr/local/node/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin SIGNIN_TG_BOT_TOKEN='xxx' SIGNIN_TG_CHAT_ID='xxx' /usr/local/node/bin/node /path/to/nodeseek-signin.mjs >> /tmp/nodeseek-cron.log 2>&1
```

## 依赖提示

- HOHAI：需要 SeleniumBase 环境、可用 Chrome，以及 headed 模式下的显示（如 `DISPLAY=:1`）。机房 IP 建议配置 `HOHAI_PROXY`（需同时能访问站点与 `challenges.cloudflare.com`）。
- NodeLoc：需要 `patchright` 和可用 Chrome/CDP。
- NodeSeek：需要 Node.js 运行时支持 `fetch` 和 `WebSocket`，并能访问 Chrome CDP。

## 安全

不要把真实账号、密码、Bot Token、Chat ID、代理账号写进公开仓库；请使用环境变量或本机私有配置（如 `chmod 600` 的 env 文件）。
