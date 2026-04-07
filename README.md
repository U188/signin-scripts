# signin-scripts

签到脚本集合。

## 脚本列表
- `hohai-sb.py`：HOHAI 自动签到（Python + SeleniumBase）
- `nodeloc_signin.py`：NodeLoc 自动签到（Python + Patchright）
- `nodeseek_signin.mjs`：NodeSeek 自动签到（Node.js + Chrome CDP）

## 设计原则
- 仓库内保存的是完整脚本本体
- 调度层（如 cron）只负责定时调用脚本，不负责决定脚本策略
- 每个脚本都应自行决定默认执行链路、输出格式、失败提示

---

## HOHAI

### 文件
- `hohai-sb.py`

### 依赖
- Python 3
- `seleniumbase`
- Chrome / Chromium
- 可复用的持久用户目录

### 默认行为
- 脚本本体默认使用可见浏览器模式
- 复用持久 profile
- 遇到 Turnstile 时会等待验证弹窗稳定后再点，并在最终刷新页面确认签到状态

### 可选环境变量
- `HOHAI_USERNAME`
- `HOHAI_PASSWORD`
- `HOHAI_SB_PROFILE`
- `HOHAI_HEADED`

### 运行示例
```bash
python3 hohai-sb.py
```

---

## NodeLoc

### 文件
- `nodeloc_signin.py`

### 依赖
- Python 3
- `patchright`
- Chrome / Chromium
- 可连接的 Chrome CDP（默认 `127.0.0.1:18800`）

### 默认行为
- 先检查 `127.0.0.1:18800` 是否已有可用 CDP
- 没有时，脚本会自行拉起临时 Chrome
- 自动登录、检查签到状态、点击签到并输出最终结果

### 可选环境变量
- `NODELOC_USERNAME`
- `NODELOC_PASSWORD`
- `CDP_URL`
- `CHROME_BIN`
- `CHROME_USER_DATA_DIR`
- `NODELOC_FALLBACK_USER_DATA_DIR`

### 运行示例
```bash
python3 nodeloc_signin.py
```

---

## NodeSeek

### 文件
- `nodeseek_signin.mjs`

### 依赖
- Node.js 18+
- Chrome / Chromium
- 可连接的 Chrome CDP HTTP 端点（默认 `http://127.0.0.1:18800`）
- 如果本机装了 OpenClaw，可由脚本尝试调用 `openclaw browser start` 启动浏览器

### 默认行为
- 先检查 CDP 是否可用
- 如果配置了 OpenClaw，可尝试调用 `openclaw browser start`
- 通过 CDP 打开 NodeSeek 页面、判断登录态、执行签到、尝试“试试手气”、提取鸡腿和排名
- 输出适合 cron / 通知脚本消费的文本结果

### 可选环境变量
- `OPENCLAW_BIN`
- `OPENCLAW_CDP_HTTP`

### 当前限制
- 脚本依赖本机存在可连接的 Chrome CDP 环境
- 如果没有 OpenClaw，也没有预先启动的 CDP Chrome，则无法直接运行
- 这时需要先手动启动带远程调试端口的 Chrome，例如：

```bash
google-chrome \
  --remote-debugging-port=18800 \
  --user-data-dir=/path/to/chrome-profile \
  --no-first-run \
  --no-default-browser-check
```

### 运行示例
```bash
node nodeseek_signin.mjs
```

### 输出约定
- 成功签到：输出 `[签到成功] NodeSeek`
- 今日已签：输出 `[重复签到] NodeSeek`
- 登录阻塞 / 验证阻塞：输出 `[执行受阻] NodeSeek`
- 脚本异常：输出 `[执行失败] NodeSeek`
