# signin-scripts

三个站点的自动签到脚本：HOHAI、NodeLoc、NodeSeek。

> 说明：仓库中的脚本已脱敏，账号、密码和 Telegram Bot 信息均通过环境变量传入。

## 文件

| 文件 | 站点 | 运行时 |
|---|---|---|
| `hohai-sb.py` | HOHAI | Python + SeleniumBase |
| `nodeloc-signin.py` | NodeLoc | Python + patchright |
| `nodeseek-signin.mjs` | NodeSeek | Node.js + Chrome CDP |

## 环境变量

### 通用 Telegram 通知

```bash
export SIGNIN_TG_BOT_TOKEN='你的 Telegram Bot Token'
export SIGNIN_TG_CHAT_ID='你的 Telegram Chat ID'
```

如果不设置这两个变量，脚本仍会在终端输出结果，但不会发送 Telegram 通知。

### HOHAI

```bash
export HOHAI_USERNAME='你的 HOHAI 用户名'
export HOHAI_PASSWORD='你的 HOHAI 密码'
export HOHAI_SB_PROFILE='/root/.config/seleniumbase-hohai'
```

运行：

```bash
/root/.openclaw/venvs/seleniumbase/bin/python hohai-sb.py
```

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

## crontab 示例

需要 GUI/Chrome 的环境建议显式传入：

```cron
35 8,20 * * * DISPLAY=:1 XDG_RUNTIME_DIR=/tmp/runtime-root XAUTHORITY=/root/.Xauthority HOME=/root PATH=/usr/local/node/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOHAI_USERNAME='xxx' HOHAI_PASSWORD='xxx' SIGNIN_TG_BOT_TOKEN='xxx' SIGNIN_TG_CHAT_ID='xxx' /root/.openclaw/venvs/seleniumbase/bin/python /path/to/hohai-sb.py >> /tmp/hohai-cron.log 2>&1

10 8,20 * * * DISPLAY=:1 XDG_RUNTIME_DIR=/tmp/runtime-root XAUTHORITY=/root/.Xauthority HOME=/root PATH=/usr/local/node/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin NODELOC_USERNAME='xxx' NODELOC_PASSWORD='xxx' SIGNIN_TG_BOT_TOKEN='xxx' SIGNIN_TG_CHAT_ID='xxx' python3 /path/to/nodeloc-signin.py >> /tmp/nodeloc-cron.log 2>&1

50 8,20 * * * HOME=/root PATH=/usr/local/node/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin SIGNIN_TG_BOT_TOKEN='xxx' SIGNIN_TG_CHAT_ID='xxx' /usr/local/node/bin/node /path/to/nodeseek-signin.mjs >> /tmp/nodeseek-cron.log 2>&1
```

## 依赖提示

- HOHAI：需要 SeleniumBase 环境和可用 Chrome。
- NodeLoc：需要 `patchright` 和可用 Chrome/CDP。
- NodeSeek：需要 Node.js 运行时支持 `fetch` 和 `WebSocket`，并能访问 Chrome CDP。

## 安全

不要把真实账号、密码、Bot Token、Chat ID 写进公开仓库；请使用环境变量或本机私有配置。
