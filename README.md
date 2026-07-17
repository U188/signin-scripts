# signin-scripts

四个站点的自动签到脚本：HOHAI、VPS8、NodeLoc、NodeSeek。

> 说明：仓库中的脚本已脱敏，账号、密码和 Telegram Bot 信息均通过环境变量传入。

## 文件

| 文件 | 站点 | 运行时 |
|---|---|---|
| `hohai-sb.py` | HOHAI | Python + SeleniumBase UC（Turnstile） |
| `run-hohai-signin.sh` | HOHAI 定时包装 | bash（source env + flock + 日志） |
| `vps8-signin.py` | VPS8（vps8.zz.cd） | Python + SeleniumBase UC（NodeLoc OAuth + reCAPTCHA v2） |
| `run-vps8-signin.sh` | VPS8 定时包装 | bash（source env + flock + 日志） |
| `nodeloc-signin.py` | NodeLoc | Python + patchright |
| `nodeseek-signin.mjs` | NodeSeek | Node.js + Chrome CDP |

## 环境变量

### 通用 Telegram 通知

```bash
export SIGNIN_TG_BOT_TOKEN='YOUR_TELEGRAM_BOT_TOKEN'
export SIGNIN_TG_CHAT_ID='YOUR_TELEGRAM_CHAT_ID'
```

如果不设置这两个变量，脚本仍会在终端输出结果，但不会发送 Telegram 通知。

`hohai-sb.py` 额外兼容 Hermes 通用变量：`TELEGRAM_BOT_TOKEN`、`TELEGRAM_ALLOWED_USERS`（取第一个 chat id）。

### HOHAI

```bash
export HOHAI_USERNAME='YOUR_HOHAI_USERNAME'
export HOHAI_PASSWORD='YOUR_HOHAI_PASSWORD'
export HOHAI_SB_PROFILE='/root/.config/seleniumbase-hohai'
export DISPLAY=:1

# 推荐：每次运行从免费代理 API 动态拉取并测活（默认开启）
export HOHAI_PROXY_API=1
# export HOHAI_PROXY_PROTOCOLS=socks5,http
# export HOHAI_PROXY_PROBE_LIMIT=80      # 每源最多测多少条
# export HOHAI_PROXY_MAX_ALIVE=8         # 测活后最多保留多少可用代理
# export HOHAI_PROXY_WORKERS=40
# export HOHAI_PROXY_TIMEOUT=6
# export HOHAI_ALLOW_DIRECT=0            # 机房直连 Turnstile 常失败，默认不回落直连

# 可选：静态优先代理（排在 API 测活结果前面）
# SeleniumBase：user:pass@host:port / host:port / socks5://host:port
# export HOHAI_PROXY='user:pass@host:port'
# export HOHAI_PROXY_LIST='host1:port,socks5://host2:port'

# 可选：自定义代理 API 源（| 或换行分隔）
# export HOHAI_PROXY_API_URLS='https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks5|https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/socks5/data.txt'

# 通知策略（默认：新签成功 / 失败才推 TG；已签到静默，避免双时段刷屏）
# export HOHAI_NOTIFY=1
# export HOHAI_NOTIFY_ON_ALREADY=0
# export HOHAI_NOTIFY_VERBOSE=0
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
# HOHAI_PROXY_API=1
# DISPLAY=:1
# SIGNIN_TG_BOT_TOKEN=...
# SIGNIN_TG_CHAT_ID=...

./run-hohai-signin.sh
```

流程：拉取免费代理 API → 并发测活（必须同时通 HOHAI 与 `challenges.cloudflare.com`）→ 按可用代理依次签到。  
成功判定以 `POST /api/checkin` 响应为准；验证失败会自动重开签到弹窗再试。

默认代理源：ProxyScrape、Proxifly、TheSpeedX、monosans、hookzof、Geonode。

### VPS8（vps8.zz.cd）

```bash
export NODELOC_USERNAME='YOUR_NODELOC_USERNAME'
export NODELOC_PASSWORD='YOUR_NODELOC_PASSWORD'
export VPS8_BASE='https://vps8.zz.cd'
export VPS8_SB_PROFILE='/root/.config/seleniumbase-vps8'
export DISPLAY=:1

# reCAPTCHA v2 图片题需要打码（否则 exit 3）
# export YESCAPTCHA_API_KEY='你的 YesCaptcha clientKey'
# export YESCAPTCHA_ENDPOINT='https://api.yescaptcha.com'  # 或 https://cn.yescaptcha.com

# 默认 OAuth/登录直连；仅在确有需要时设代理
# export VPS8_PROXY='user:pass@host:port'
```

流程（实测必需）：

1. **先完整登录 NodeLoc**（`www.nodeloc.com`）
2. 再在 vps8 `/login` 点 **Nodeloc** OAuth → `/dashboard`
3. 打开 `/points/signin`，过 reCAPTCHA 后 `POST /api/client/points/signin`

冷启动 OAuth（未先登录 NodeLoc）常见失败：`Nodeloc request failed: Operation timed out...`

运行：

```bash
# 私有 env：/root/.config/vps8-signin.env（chmod 600，勿提交）
./run-vps8-signin.sh
```

退出码：`0` 成功/已签到 · `2` 登录/OAuth 失败 · `3` CAPTCHA（缺 key 或打码失败） · `4` API 错误 · `1` 其它。

Hermes 定时（有 YesCaptcha key 后再开，建议避开 HOHAI 整点）：

```bash
hermes cron create '5 8 * * *' \
  --name vps8-signin-daily-0805 \
  --script run-vps8-signin.sh \
  --no-agent \
  --deliver local
```

### NodeLoc

```bash
export NODELOC_USERNAME='YOUR_NODELOC_USERNAME'
export NODELOC_PASSWORD='YOUR_NODELOC_PASSWORD'
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

若使用 Hermes Agent，也可（**纯程序，不经 AI**；`--no-agent` + `--deliver local`）：

```bash
# 建议双时段兜底；已签到默认不发 TG
hermes cron create '0 8 * * *' \
  --name hohai-signin-daily-0800 \
  --script run-hohai-signin.sh \
  --no-agent \
  --deliver local

hermes cron create '0 20 * * *' \
  --name hohai-signin-daily-2000 \
  --script run-hohai-signin.sh \
  --no-agent \
  --deliver local

# 多代理 headed Chrome 建议：
# hermes config set cron.script_timeout_seconds 600
```

（需把 `run-hohai-signin.sh` 与 `hohai-sb.py` 放到 `~/.hermes/scripts/`，或让包装脚本指向 Desktop 路径。）

### 通用 crontab（直接传环境变量）

需要 GUI/Chrome 的环境建议显式传入：

```cron
0 8 * * * DISPLAY=:1 HOME=/root PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOHAI_USERNAME='xxx' HOHAI_PASSWORD='xxx' HOHAI_PROXY='user:pass@host:port' SIGNIN_TG_BOT_TOKEN='xxx' SIGNIN_TG_CHAT_ID='xxx' /root/.openclaw/venvs/seleniumbase/bin/python /path/to/hohai-sb.py >> /tmp/hohai-cron.log 2>&1

10 8,20 * * * DISPLAY=:1 HOME=/root PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin NODELOC_USERNAME='xxx' NODELOC_PASSWORD='xxx' SIGNIN_TG_BOT_TOKEN='xxx' SIGNIN_TG_CHAT_ID='xxx' python3 /path/to/nodeloc-signin.py >> /tmp/nodeloc-cron.log 2>&1

50 8,20 * * * HOME=/root PATH=/usr/local/node/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin SIGNIN_TG_BOT_TOKEN='xxx' SIGNIN_TG_CHAT_ID='xxx' /usr/local/node/bin/node /path/to/nodeseek-signin.mjs >> /tmp/nodeseek-cron.log 2>&1
```

## 依赖提示

- HOHAI：需要 SeleniumBase 环境、可用 Chrome，以及 headed 模式下的显示（如 `DISPLAY=:1`）。机房 IP 建议 `HOHAI_PROXY_API=1` 动态测活（需同时能访问站点与 `challenges.cloudflare.com`）。
- VPS8：SeleniumBase UC + NodeLoc 账号；自动过 reCAPTCHA v2 需 `YESCAPTCHA_API_KEY`。
- NodeLoc：需要 `patchright` 和可用 Chrome/CDP。
- NodeSeek：需要 Node.js 运行时支持 `fetch` 和 `WebSocket`，并能访问 Chrome CDP。

## 安全

不要把真实账号、密码、Bot Token、Chat ID、代理账号、YesCaptcha key 写进公开仓库；请使用环境变量或本机私有配置（如 `chmod 600` 的 `/root/.config/*-signin.env`）。
