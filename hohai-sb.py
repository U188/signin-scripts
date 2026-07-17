#!/root/.openclaw/venvs/seleniumbase/bin/python
# -*- coding: utf-8 -*-
"""HOHAI 自动签到（SeleniumBase UC + Turnstile GUI 点击）。

环境变量：
  HOHAI_USERNAME / HOHAI_PASSWORD   必填
  HOHAI_PROXY                       可选静态代理：user:pass@host:port / host:port / socks5://host:port
  HOHAI_PROXY_LIST                  可选静态多代理（逗号/换行），优先于 API 拉取结果之前插入
  HOHAI_PROXY_API                   默认 1：从免费代理 API 动态拉取并测活
  HOHAI_PROXY_API_URLS              可选，| 或换行分隔的自定义 API URL（覆盖默认源）
  HOHAI_PROXY_PROTOCOLS             默认 socks5,http（测活顺序）
  HOHAI_PROXY_PROBE_LIMIT           每个源最多测多少条，默认 120
  HOHAI_PROXY_MAX_ALIVE             测活后最多保留多少可用代理，默认 12
  HOHAI_PROXY_WORKERS               测活并发，默认 40
  HOHAI_PROXY_TIMEOUT               单代理测活超时秒，默认 6
  HOHAI_ALLOW_DIRECT                默认 0：免费代理全失败时是否回落直连（机房 IP 易被 Turnstile 拒）
  HOHAI_SB_PROFILE                  浏览器 profile 目录
  HOHAI_HEADED                      默认 1
  HOHAI_KEEP_OPEN_ON_FAIL           失败时是否保留浏览器
  HOHAI_OBSERVE_MODE                观察模式
  HOHAI_HOLD_OPEN_SECS              观察保留秒数
  SIGNIN_TG_BOT_TOKEN / SIGNIN_TG_CHAT_ID  通知（也兼容 TELEGRAM_BOT_TOKEN / TELEGRAM_ALLOWED_USERS）
  HOHAI_NOTIFY              默认 1：是否发 Telegram
  HOHAI_NOTIFY_ON_ALREADY   默认 0：今日已签到时不通知（避免双时段刷屏）
  HOHAI_NOTIFY_VERBOSE      默认 0：简洁通知；1=带 API/调试细节
"""

import concurrent.futures
import json
import os
import random
import re
import sys
import time
import traceback
import urllib.parse
import urllib.request
from datetime import datetime

from seleniumbase import SB

BASE = "https://tv.hohai.eu.org"
LOGIN_URL = f"{BASE}/login"
DASHBOARD_URL = f"{BASE}/dashboard"
CHECKIN_API = f"{BASE}/api/checkin"

USERNAME = os.environ.get("HOHAI_USERNAME", "")
PASSWORD = os.environ.get("HOHAI_PASSWORD", "")
PROFILE_DIR = os.environ.get("HOHAI_SB_PROFILE", "/root/.config/seleniumbase-hohai")
HEADED = os.environ.get("HOHAI_HEADED", "1") == "1"
KEEP_OPEN_ON_FAIL = os.environ.get("HOHAI_KEEP_OPEN_ON_FAIL", "0") == "1"
OBSERVE_MODE = os.environ.get("HOHAI_OBSERVE_MODE", "0") == "1"
HOLD_OPEN_SECS = int(os.environ.get("HOHAI_HOLD_OPEN_SECS", "600"))

# 通知策略：默认只在「新签成功 / 真正失败」时推送，已签到静默
def _env_bool(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on", "y")

NOTIFY_ENABLED = _env_bool("HOHAI_NOTIFY", True)
NOTIFY_ON_ALREADY = _env_bool("HOHAI_NOTIFY_ON_ALREADY", False)
NOTIFY_VERBOSE = _env_bool("HOHAI_NOTIFY_VERBOSE", False)

# Telegram：优先 SIGNIN_*，回退 Hermes 通用变量
TELEGRAM_BOT_TOKEN = (
    os.environ.get("SIGNIN_TG_BOT_TOKEN")
    or os.environ.get("TELEGRAM_BOT_TOKEN")
    or ""
)
TELEGRAM_CHAT_ID = (
    os.environ.get("SIGNIN_TG_CHAT_ID")
    or os.environ.get("TELEGRAM_ALLOWED_USERS", "").split(",")[0].strip()
    or ""
)

SUCCESS_TEXTS = ["已签到", "今日已签到", "签到成功"]
MODAL_TEXTS = ["签到验证", "请完成人机验证以继续签到"]
FAILURE_TEXTS = ["Turnstile验证失败", "Validation failed", "验证失败", "人机验证失败"]
GUI_TARGET_SELECTORS = [
    'iframe[src*="challenge-platform"]',
    'iframe[src*="turnstile"]',
    ".turnstile-widget",
    ".cloudflare-turnstile-container",
    '[id^="verification-checkin-"]',
]



DEFAULT_PROXY_API_SOURCES = [
    # ProxyScrape free public API
    ("proxyscrape_socks5", "socks5", "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks5&timeout=10000&country=all"),
    ("proxyscrape_http", "http", "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=8000&country=all"),
    # Proxifly GitHub mirror
    ("proxifly_socks5", "socks5", "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/socks5/data.txt"),
    ("proxifly_http", "http", "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/http/data.txt"),
    # GitHub raw lists
    ("speedx_socks5", "socks5", "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt"),
    ("monosans_socks5", "socks5", "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt"),
    ("hookzof_socks5", "socks5", "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt"),
    # Geonode JSON
    ("geonode_socks5", "socks5", "https://proxylist.geonode.com/api/proxy-list?limit=200&page=1&sort_by=lastChecked&sort_type=desc&protocols=socks5"),
]


def env_bool(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on", "y")


def env_int(name, default):
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except Exception:
        return default


def detect_proxy_scheme(raw):
    p = (raw or "").strip().lower()
    if p.startswith("socks5h://"):
        return "socks5h"
    if p.startswith("socks5://"):
        return "socks5"
    if p.startswith("socks4://"):
        return "socks4"
    if p.startswith("https://"):
        return "https"
    if p.startswith("http://"):
        return "http"
    return None


def normalize_proxy(raw, default_scheme=None):
    """Return SeleniumBase-ready proxy string.

    - bare host:port → keep bare (Chrome defaults to HTTP) unless default_scheme given
    - socks5://host:port → keep scheme (required for free SOCKS lists)
    - user:pass@host:port → keep as-is
    """
    if not raw:
        return None
    p = raw.strip()
    scheme = detect_proxy_scheme(p)
    if scheme:
        body = p.split("://", 1)[1]
    else:
        body = p
        scheme = default_scheme
    body = body.strip()
    if not body:
        return None
    # strip leftover scheme fragments
    if "://" in body:
        body = body.split("://", 1)[1]
    # validate host:port shape roughly
    hostport = body.split("@")[-1]
    if not re.match(r"^[\w.\[\]:-]+:\d+$", hostport):
        # allow host names / ipv6-ish loosely; if no port, reject
        if ":" not in hostport:
            return None
    if scheme in ("socks5", "socks5h", "socks4"):
        # SeleniumBase/Chrome: socks5://host:port  (auth rare for free lists)
        if "@" in body:
            # user:pass@host:port with socks — keep scheme
            return f"{scheme}://{body}"
        return f"{scheme}://{body}"
    if scheme in ("http", "https"):
        # SB auth form prefers user:pass@host:port without scheme
        return body
    # unknown / bare
    return body


def proxy_label(proxy):
    return proxy or "direct"


def parse_proxy_candidates(text, default_scheme="socks5"):
    """Extract host:port candidates from plain/json/html-ish text."""
    if not text:
        return []
    items = []
    # JSON Geonode-like
    if '"ip"' in text and '"port"' in text:
        try:
            data = json.loads(text)
            rows = data.get("data") if isinstance(data, dict) else data
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    ip = str(row.get("ip") or row.get("host") or "").strip()
                    port = str(row.get("port") or "").strip()
                    if ip and port.isdigit():
                        items.append((default_scheme, f"{ip}:{port}"))
        except Exception:
            pass
    # generic host:port
    for ip, port in re.findall(r"(\d{1,3}(?:\.\d{1,3}){3}):(\d{2,5})", text):
        items.append((default_scheme, f"{ip}:{port}"))
    # scheme-prefixed
    for scheme, hostport in re.findall(r"(socks5h?|socks4|https?)://([\w.\[\]:-]+:\d+)", text, flags=re.I):
        items.append((scheme.lower().replace("socks5h", "socks5"), hostport))
    # dedupe preserve order
    seen = set()
    out = []
    for scheme, hostport in items:
        key = (scheme, hostport)
        if key in seen:
            continue
        seen.add(key)
        out.append((scheme, hostport))
    return out


def http_get_text(url, timeout=20):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) HOHAI-Signin/1.0",
            "Accept": "*/*",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


def fetch_proxy_api_sources():
    custom = os.environ.get("HOHAI_PROXY_API_URLS", "").strip()
    if custom:
        sources = []
        for i, part in enumerate(re.split(r"[\n|]+", custom)):
            url = part.strip()
            if not url:
                continue
            # infer protocol from url keywords
            low = url.lower()
            scheme = "socks5" if "socks5" in low or "socks" in low else "http"
            sources.append((f"custom_{i+1}", scheme, url))
        return sources
    # filter by HOHAI_PROXY_PROTOCOLS
    wanted = {x.strip().lower() for x in re.split(r"[,;\s]+", os.environ.get("HOHAI_PROXY_PROTOCOLS", "socks5,http")) if x.strip()}
    if not wanted:
        wanted = {"socks5", "http"}
    out = []
    for name, scheme, url in DEFAULT_PROXY_API_SOURCES:
        if scheme in wanted or (scheme == "socks5" and "socks5" in wanted):
            out.append((name, scheme, url))
    return out


def fetch_proxies_from_apis(limit_per_source=120):
    sources = fetch_proxy_api_sources()
    collected = []
    stats = []
    for name, scheme, url in sources:
        try:
            body = http_get_text(url, timeout=20)
            cands = parse_proxy_candidates(body, default_scheme=scheme)[: max(1, limit_per_source)]
            stats.append({"source": name, "ok": True, "count": len(cands), "url": url[:120]})
            for sch, hostport in cands:
                collected.append((sch, hostport, name))
        except Exception as e:
            stats.append({"source": name, "ok": False, "error": f"{type(e).__name__}: {e}", "url": url[:120]})
    print(json.dumps({"event": "proxy_api_fetch", "sources": stats, "total_raw": len(collected)}, ensure_ascii=False))
    # dedupe by scheme+hostport
    seen = set()
    uniq = []
    for sch, hostport, src in collected:
        key = (sch, hostport)
        if key in seen:
            continue
        seen.add(key)
        uniq.append((sch, hostport, src))
    return uniq


def _curl_code(proxy_url, target_url, timeout):
    try:
        # local curl binary; avoid shell
        import subprocess

        r = subprocess.run(
            [
                "curl",
                "-sS",
                "-m",
                str(timeout),
                "-x",
                proxy_url,
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                "--max-redirs",
                "3",
                "-L",
                target_url,
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 3,
        )
        return (r.stdout or "").strip()
    except Exception:
        return "000"


def probe_one_proxy(scheme, hostport, source, timeout=6):
    if scheme.startswith("socks"):
        proxy_url = f"socks5h://{hostport}"
        sb_proxy = normalize_proxy(hostport, default_scheme="socks5")
    else:
        proxy_url = f"http://{hostport}"
        sb_proxy = normalize_proxy(hostport, default_scheme="http")

    # Require BOTH hohai site and Cloudflare challenges (Turnstile path)
    hohai = _curl_code(proxy_url, BASE + "/", timeout)
    if hohai != "200":
        return None
    cf = _curl_code(proxy_url, "https://challenges.cloudflare.com/", timeout)
    if cf != "200":
        return None
    # optional ip check (not required)
    ip_code = _curl_code(proxy_url, "https://api.ipify.org", timeout)
    return {
        "proxy": sb_proxy,
        "hostport": hostport,
        "scheme": scheme,
        "source": source,
        "hohai": hohai,
        "cf": cf,
        "ip": ip_code,
    }


def probe_proxies(candidates, max_alive=12, workers=40, timeout=6):
    if not candidates:
        return []
    alive = []
    t0 = time.time()
    # Prefer socks5 first in candidate order
    candidates = sorted(candidates, key=lambda x: 0 if str(x[0]).startswith("socks") else 1)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(4, workers)) as ex:
        futs = {
            ex.submit(probe_one_proxy, sch, hostport, src, timeout): (sch, hostport, src)
            for sch, hostport, src in candidates
        }
        for fut in concurrent.futures.as_completed(futs):
            try:
                row = fut.result()
            except Exception:
                row = None
            if not row:
                continue
            alive.append(row)
            print(json.dumps({"event": "proxy_alive", **row}, ensure_ascii=False))
            if len(alive) >= max_alive:
                # cancel remaining
                for f in futs:
                    f.cancel()
                break
    print(
        json.dumps(
            {
                "event": "proxy_probe_done",
                "alive": len(alive),
                "probed_pool": len(candidates),
                "elapsed_s": round(time.time() - t0, 1),
                "proxies": [a["proxy"] for a in alive],
            },
            ensure_ascii=False,
        )
    )
    return alive


def static_proxies_from_env():
    items = []
    single = normalize_proxy(os.environ.get("HOHAI_PROXY", ""))
    if single:
        items.append(single)
    blob = os.environ.get("HOHAI_PROXY_LIST", "") or ""
    for part in re.split(r"[\n,;]+", blob):
        n = normalize_proxy(part)
        if n and n not in items:
            items.append(n)
    return items


def build_proxy_queue():
    """Build ordered proxy queue for sign-in attempts.

    Order:
      1) static HOHAI_PROXY / HOHAI_PROXY_LIST (if set)
      2) dynamically fetched + probed free proxies (if HOHAI_PROXY_API=1)
      3) optional direct (HOHAI_ALLOW_DIRECT=1)
    """
    queue = []
    for p in static_proxies_from_env():
        if p not in queue:
            queue.append(p)

    use_api = env_bool("HOHAI_PROXY_API", True)
    if use_api:
        limit = env_int("HOHAI_PROXY_PROBE_LIMIT", 120)
        max_alive = env_int("HOHAI_PROXY_MAX_ALIVE", 12)
        workers = env_int("HOHAI_PROXY_WORKERS", 40)
        timeout = env_int("HOHAI_PROXY_TIMEOUT", 6)
        try:
            cands = fetch_proxies_from_apis(limit_per_source=limit)
            # cap total probe pool to keep runtime bounded
            max_pool = env_int("HOHAI_PROXY_POOL_MAX", 250)
            cands = cands[:max_pool]
            alive = probe_proxies(cands, max_alive=max_alive, workers=workers, timeout=timeout)
            for row in alive:
                p = row.get("proxy")
                if p and p not in queue:
                    queue.append(p)
        except Exception as e:
            print(json.dumps({"event": "proxy_api_error", "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))

    allow_direct = env_bool("HOHAI_ALLOW_DIRECT", False)
    if allow_direct and None not in queue:
        queue.append(None)

    if not queue:
        # last resort: still try direct so script produces a clear Turnstile failure report
        print(json.dumps({"event": "proxy_queue_empty_fallback_direct"}, ensure_ascii=False))
        queue.append(None)

    print(json.dumps({"event": "proxy_queue", "count": len(queue), "items": [proxy_label(p) for p in queue]}, ensure_ascii=False))
    return queue


def proxy_list_from_env():
    """Backward-compatible name used by main(). """
    return build_proxy_queue()


def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        data = urllib.parse.urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": text}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data=data,
            method="POST",
        )
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:
        print(f"[notify] Telegram 发送失败: {e}", file=sys.stderr)


def is_already_checked_in(message, extra=None):
    """今日已签到（非本次新签到成功）。"""
    extra = extra or {}
    if extra.get("already") or (extra.get("step") or {}).get("already"):
        return True
    msg = str(message or "")
    return any(x in msg for x in ("页面已显示已签到", "今日已签到", "已经签到", "已签到过"))


def format_report(ok, message, extra):
    """简洁通知正文。"""
    extra = extra or {}
    already = is_already_checked_in(message, extra)
    ts = datetime.now().strftime("%m-%d %H:%M")
    proxy = extra.get("proxy")
    proxy_s = (proxy or "直连") if proxy is not None else None

    if ok and already:
        lines = [f"HOHAI · 今日已签到（{ts}）"]
    elif ok:
        lines = [f"HOHAI · ✅ 签到成功（{ts}）"]
        # 优先展示 API 摘要
        c = extra.get("checkin")
        if isinstance(c, dict):
            resp = c.get("resp") if isinstance(c.get("resp"), dict) else {}
            amount = resp.get("amount")
            balance = resp.get("balance")
            unit = resp.get("currencyUnit") or "¥"
            if amount is not None:
                lines.append(f"+{amount}{unit}" + (f" · 余额 {balance}{unit}" if balance is not None else ""))
            elif c.get("message") or resp.get("message"):
                lines.append(str(c.get("message") or resp.get("message"))[:80])
        else:
            lines.append(str(message)[:80])
    else:
        lines = [f"HOHAI · ❌ 签到失败（{ts}）", str(message)[:120]]
        if extra.get("error") and NOTIFY_VERBOSE:
            lines.append(f"err: {str(extra.get('error'))[:160]}")

    if proxy_s and (NOTIFY_VERBOSE or not ok or (ok and not already)):
        # 成功新签 / 失败时带代理；已签到默认不带
        if not (ok and already) or NOTIFY_VERBOSE:
            lines.append(f"代理: {proxy_s}")

    if NOTIFY_VERBOSE:
        if extra.get("url"):
            lines.append(f"url: {extra.get('url')}")
        if extra.get("checkin") and not ok:
            c = extra["checkin"]
            if isinstance(c, dict):
                lines.append(f"api: {str(c.get('resp') or c.get('message') or '')[:120]}")
    return "\n".join(lines)


def should_notify(ok, message, extra):
    if not NOTIFY_ENABLED:
        return False
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    if is_already_checked_in(message, extra) and not NOTIFY_ON_ALREADY:
        return False
    return True


def done(ok, message, **extra):
    data = {"ok": ok, "message": message}
    data.update(extra)
    report = format_report(ok, message, extra)
    # 终端始终打印完整 JSON 便于日志排查；TG 只发简洁文案
    print(report)
    print(json.dumps(data, ensure_ascii=False, default=str))
    if should_notify(ok, message, extra):
        send_telegram(report)
    else:
        reason = "disabled" if not NOTIFY_ENABLED else (
            "already_silent" if is_already_checked_in(message, extra) else "no_token"
        )
        print(json.dumps({"event": "notify_skip", "reason": reason}, ensure_ascii=False))
    sys.exit(0 if ok else 1)


def body_text(sb):
    try:
        return sb.get_text("body")
    except Exception:
        return ""


def has_success(text):
    return any(x in text for x in SUCCESS_TEXTS)


def modal_ready(text):
    return all(x in text for x in MODAL_TEXTS)


def failure_seen(text):
    return any(x in text for x in FAILURE_TEXTS)


def token_len_from_state(state):
    return int((state or {}).get("token_len", 0) or 0)


def has_verified_token(token_len):
    return token_len > 20


def placeholder_only_state(state):
    html = re.sub(r"\s+", " ", str((state or {}).get("widget_html", "") or "")).lower()
    if not html:
        return True
    if any(tag in html for tag in ("<iframe", "<canvas", "<svg", "checkbox", "challenge")):
        return False
    return ("cf-turnstile-response" in html) and (html.count("<input") == 1) and (html.count("<div") <= 2)


def visual_checkbox_ready(rect):
    if not HEADED or not rect_is_usable(rect):
        return {"ready": False, "reason": "not_headed_or_bad_rect"}
    return {"ready": True, "reason": "rect_usable_bypass"}


def get_turnstile_state(sb):
    js = r"""
    (() => {
      const input = document.querySelector('input[name="cf-turnstile-response"], input[id^="cf-chl-widget-"][id$="_response"]');
      const widget = document.querySelector('.turnstile-widget, .cloudflare-turnstile-container, [id^="verification-checkin-"], [class*="turnstile"], [id*="turnstile"]');
      const iframe = document.querySelector('iframe[src*="turnstile"], iframe[src*="challenge-platform"]');
      const target = iframe || widget || input?.closest('.turnstile-widget, .cloudflare-turnstile-container, [id^="verification-checkin-"]') || input?.parentElement || null;
      const rect = target ? target.getBoundingClientRect() : null;
      const cx = rect ? Math.floor(rect.left + rect.width / 2) : null;
      const cy = rect ? Math.floor(rect.top + rect.height / 2) : null;
      const hit = (cx !== null && cy !== null) ? document.elementFromPoint(cx, cy) : null;
      return {
        token_len: input ? input.value.length : 0,
        has_token_input: !!input,
        has_widget: !!widget,
        has_iframe: !!iframe,
        widget_rect: rect ? {x: rect.x, y: rect.y, width: rect.width, height: rect.height} : null,
        center_hit_in_widget: !!(hit && target && (hit === target || target.contains(hit) || hit.contains(target))),
        center_hit: hit ? `${hit.tagName}#${hit.id}.${hit.className}`.slice(0, 160) : '',
        widget_html: target ? target.outerHTML.slice(0, 400) : ''
      };
    })();
    """
    try:
        return sb.execute_script(js) or {}
    except Exception as e:
        return {"error": str(e)}


def rect_is_usable(rect):
    return bool(rect and rect.get("width", 0) >= 240 and rect.get("height", 0) >= 60)


def rect_is_stable(prev_rect, rect, tolerance=4):
    if not prev_rect or not rect:
        return False
    return (
        abs(prev_rect.get("x", 0) - rect.get("x", 0)) <= tolerance
        and abs(prev_rect.get("y", 0) - rect.get("y", 0)) <= tolerance
        and abs(prev_rect.get("width", 0) - rect.get("width", 0)) <= tolerance
        and abs(prev_rect.get("height", 0) - rect.get("height", 0)) <= tolerance
    )


def get_gui_target(sb):
    for selector in GUI_TARGET_SELECTORS:
        try:
            if sb.is_element_present(selector):
                rect = sb.get_gui_element_rect(selector)
                if rect_is_usable(rect):
                    return {"selector": selector, "rect": rect}
        except Exception:
            continue
    return {"selector": None, "rect": None}


def wait_modal_and_widget(sb, timeout=12):
    rounds = int(timeout / 0.5)
    last = {
        "modal_seen": False,
        "widget_ready": False,
        "state": {},
        "body_hit": [],
        "failure_seen": False,
        "stable_hits": 0,
    }
    stable_hits = 0
    prev_rect = None
    for _ in range(rounds):
        text = body_text(sb)
        state = get_turnstile_state(sb)
        gui_target = get_gui_target(sb)
        modal_seen_now = modal_ready(text)
        failure_seen_now = failure_seen(text)
        rect = gui_target.get("rect") or state.get("widget_rect")
        widget_ready = bool(
            state.get("has_widget")
            or state.get("has_iframe")
            or state.get("has_token_input")
            or gui_target.get("selector")
        )
        center_ready = state.get("center_hit_in_widget", False) or bool(gui_target.get("selector"))
        geometry_ready = rect_is_usable(rect)
        placeholder_only = placeholder_only_state(state)
        visual_probe = visual_checkbox_ready(rect) if geometry_ready else {"ready": False, "reason": "bad_rect"}
        interactive_ready = (
            bool(state.get("has_iframe"))
            or visual_probe.get("ready", False)
            or not placeholder_only
            or geometry_ready
        )
        current_stable = modal_seen_now and widget_ready and geometry_ready and interactive_ready
        if current_stable:
            stable_hits = stable_hits + 1 if rect_is_stable(prev_rect, rect) else 1
        else:
            stable_hits = 0
        last = {
            "modal_seen": modal_seen_now,
            "failure_seen": failure_seen_now,
            "widget_ready": widget_ready,
            "geometry_ready": geometry_ready,
            "center_ready": center_ready,
            "interactive_ready": interactive_ready,
            "placeholder_only": placeholder_only,
            "visual_probe": visual_probe,
            "stable_hits": stable_hits,
            "gui_target": gui_target,
            "state": state,
            "body_hit": [x for x in MODAL_TEXTS + FAILURE_TEXTS if x in text],
        }
        if current_stable and ((failure_seen_now and stable_hits >= 2) or stable_hits >= 4):
            return last
        prev_rect = rect
        sb.sleep(0.5)
    return last


def hold_browser_for_observation(sb, reason, step):
    if not (KEEP_OPEN_ON_FAIL and HEADED):
        return
    print(
        json.dumps(
            {
                "ok": False,
                "message": reason,
                "url": sb.get_current_url(),
                "step": step,
                "observe_mode": OBSERVE_MODE,
                "hold_open_secs": HOLD_OPEN_SECS,
            },
            ensure_ascii=False,
        )
    )
    sb.sleep(HOLD_OPEN_SECS)


def install_network_hook(sb):
    """Hook fetch/XHR，捕获 /api/checkin 结果。"""
    try:
        sb.execute_script(
            r"""
            (() => {
              if (window.__hohai_net_hooked) return true;
              window.__hohai_net_hooked = true;
              window.__hohai_net = [];
              const push = (row) => {
                try {
                  window.__hohai_net.push(row);
                  if (window.__hohai_net.length > 100) window.__hohai_net.shift();
                } catch (e) {}
              };
              const ofetch = window.fetch;
              window.fetch = async function(input, init) {
                const url = (typeof input === 'string') ? input : (input && input.url) || '';
                const method = (init && init.method) || (input && input.method) || 'GET';
                let body = init && init.body;
                body = body ? String(body).slice(0, 240) : '';
                const started = Date.now();
                try {
                  const res = await ofetch.apply(this, arguments);
                  let text = '';
                  try { text = await res.clone().text(); } catch (e) {}
                  push({
                    t: Date.now(), kind: 'fetch', method, url: String(url).slice(0, 220),
                    status: res.status, body, resp: String(text).slice(0, 400), ms: Date.now() - started
                  });
                  return res;
                } catch (err) {
                  push({t: Date.now(), kind: 'fetch', method, url: String(url).slice(0, 220), error: String(err), body});
                  throw err;
                }
              };
              return true;
            })()
            """
        )
    except Exception as e:
        print(f"[net] hook install failed: {e}", file=sys.stderr)


def pop_checkin_results(sb):
    try:
        net = sb.execute_script("(() => window.__hohai_net || [])()") or []
    except Exception:
        net = []
    out = []
    for n in net:
        url = str(n.get("url") or "")
        if "/api/checkin" in url:
            out.append(n)
    return out


def latest_checkin(sb):
    items = pop_checkin_results(sb)
    return items[-1] if items else None


def parse_checkin_ok(entry):
    if not entry:
        return False, None
    status = entry.get("status")
    resp = entry.get("resp") or ""
    try:
        data = json.loads(resp) if resp else {}
    except Exception:
        data = {"raw": resp}
    if status in (200, 201) and (data.get("success") is True or "签到成功" in resp or data.get("hasCheckedIn")):
        return True, {"status": status, "resp": data if data else resp}
    return False, {"status": status, "resp": data if data else resp, "message": (data or {}).get("message") or resp}


def visible_click_turnstile(sb):
    import pyautogui

    target = get_gui_target(sb)
    selector = target.get("selector")
    rect = target.get("rect")
    if not selector or not rect_is_usable(rect):
        return {"clicked": False, "reason": "no_stable_gui_target"}

    left_bias_x = rect["x"] + (rect["width"] * 0.12)
    center_y = rect["y"] + (rect["height"] * 0.5)
    click_x = int(max(rect["x"] + 8, min(left_bias_x + random.randint(-6, 6), rect["x"] + rect["width"] - 8)))
    click_y = int(max(rect["y"] + 8, min(center_y + random.randint(-4, 4), rect["y"] + rect["height"] - 8)))

    pyautogui.moveTo(click_x, click_y, duration=0.35)
    sb.sleep(0.45)
    pyautogui.mouseDown(x=click_x, y=click_y)
    sb.sleep(0.18)
    pyautogui.mouseUp(x=click_x, y=click_y)
    return {
        "clicked": True,
        "selector": selector,
        "rect": rect,
        "point": {"x": click_x, "y": click_y},
        "target_area": "left_checkbox_bias",
    }


def page_looks_like_cf_challenge(sb):
    """粗判 Cloudflare / 加载中页面（输入框尚未出现）。"""
    try:
        title = (sb.get_title() or "").lower()
    except Exception:
        title = ""
    try:
        text = (body_text(sb) or "")[:2000]
    except Exception:
        text = ""
    markers = [
        "just a moment",
        "checking your browser",
        "attention required",
        "cf-browser-verification",
        "challenge-platform",
        "请完成安全验证",
        "正在验证",
        "enable javascript and cookies",
    ]
    blob = f"{title}\n{text}".lower()
    return any(m in blob for m in markers)


def try_pass_cf_interstitial(sb):
    """尝试点掉登录前的 CF 挑战（best-effort）。"""
    actions = []
    # SeleniumBase UC 原生
    for name in ("uc_gui_click_cf", "uc_gui_handle_cf", "uc_gui_click_captcha"):
        fn = getattr(sb, name, None)
        if callable(fn):
            try:
                fn()
                actions.append(name)
                sb.sleep(1.2)
            except Exception as e:
                actions.append(f"{name}:err:{type(e).__name__}")
    # 通用 pyautogui 点中间偏左（常见 checkbox 区域）
    if HEADED:
        try:
            import pyautogui
            # 优先找 challenge iframe 几何
            rect = None
            for sel in (
                'iframe[src*="challenge-platform"]',
                'iframe[src*="turnstile"]',
                "#challenge-stage",
                ".cf-turnstile",
            ):
                try:
                    if sb.is_element_present(sel):
                        rect = sb.get_gui_element_rect(sel)
                        if rect and rect.get("width", 0) > 20:
                            break
                except Exception:
                    continue
            if rect and rect.get("width", 0) > 20:
                x = int(rect["x"] + min(40, max(12, rect["width"] * 0.15)))
                y = int(rect["y"] + rect["height"] * 0.5)
            else:
                # 屏幕中部偏上
                x, y = 540, 420
            pyautogui.moveTo(x, y, duration=0.25)
            sb.sleep(0.2)
            pyautogui.click(x=x, y=y)
            actions.append(f"pyautogui:{x},{y}")
            sb.sleep(1.5)
        except Exception as e:
            actions.append(f"pyautogui:err:{type(e).__name__}")
    return actions


def wait_for_login_inputs(sb, timeout=45):
    deadline = time.time() + timeout
    last = {
        "cdp_count": 0,
        "selenium_count": 0,
        "js_count": 0,
        "url": "",
        "cf_seen": False,
        "reloads": 0,
        "cf_actions": [],
    }
    next_cf_try = 0.0
    next_reload_at = time.time() + 18
    while time.time() < deadline:
        last["url"] = sb.get_current_url()
        # 已不在登录页（cookie 生效）→ 上层会处理
        if "/login" not in (last["url"] or ""):
            last["left_login"] = True
            return {"mode": None, "inputs": [], "state": last}

        cf_now = page_looks_like_cf_challenge(sb)
        last["cf_seen"] = last["cf_seen"] or cf_now
        if cf_now and time.time() >= next_cf_try:
            acts = try_pass_cf_interstitial(sb)
            if acts:
                last["cf_actions"].extend(acts)
            next_cf_try = time.time() + 4

        try:
            inputs = sb.cdp.find_elements("input")
            last["cdp_count"] = len(inputs)
            if len(inputs) >= 2:
                return {"mode": "cdp", "inputs": inputs, "state": last}
        except Exception as e:
            last["cdp_error"] = str(e)[:160]

        try:
            inputs = sb.find_elements("input")
            last["selenium_count"] = len(inputs)
            if len(inputs) >= 2:
                return {"mode": "selenium", "inputs": inputs, "state": last}
        except Exception as e:
            last["selenium_error"] = str(e)[:160]

        try:
            js_inputs = sb.execute_script(
                r"""
                (() => Array.from(document.querySelectorAll('input')).map((el, index) => ({
                    index,
                    type: el.type || '',
                    name: el.name || '',
                    id: el.id || '',
                    placeholder: el.placeholder || '',
                    autocomplete: el.autocomplete || '',
                    visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
                })))()
                """
            ) or []
            last["js_count"] = len(js_inputs)
            if len(js_inputs) >= 2:
                return {"mode": "js", "inputs": js_inputs, "state": last}
        except Exception as e:
            last["js_error"] = str(e)[:160]

        # 长时间无输入框：刷新一次（最多 2 次）
        if time.time() >= next_reload_at and last["reloads"] < 2 and last["js_count"] < 2:
            try:
                sb.open(LOGIN_URL)
                last["reloads"] += 1
                next_reload_at = time.time() + 16
                sb.sleep(2)
            except Exception as e:
                last["reload_error"] = str(e)[:120]

        sb.sleep(0.75)
    return {"mode": None, "inputs": [], "state": last}


def pick_login_input_indices(js_inputs):
    user_idx = None
    pwd_idx = None
    for item in js_inputs:
        text = " ".join(str(item.get(k, "")) for k in ["type", "name", "id", "placeholder", "autocomplete"]).lower()
        if pwd_idx is None and ("password" in text or item.get("type") == "password"):
            pwd_idx = item.get("index")
        if user_idx is None and any(
            k in text for k in ["user", "email", "mail", "account", "phone", "name", "登录", "账号", "邮箱", "手机号"]
        ):
            user_idx = item.get("index")
    visible = [item.get("index") for item in js_inputs if item.get("visible")]
    ordered = visible or [item.get("index") for item in js_inputs]
    if user_idx is None and ordered:
        user_idx = ordered[0]
    if pwd_idx is None:
        for idx in ordered:
            if idx != user_idx:
                pwd_idx = idx
                break
    return user_idx, pwd_idx


def fill_login_inputs(sb, login_probe):
    mode = login_probe.get("mode")
    inputs = login_probe.get("inputs") or []
    if mode in ("cdp", "selenium"):
        inputs[0].click()
        sb.sleep(0.4)
        inputs[0].press_keys(USERNAME)
        sb.sleep(0.4)
        inputs[1].click()
        sb.sleep(0.4)
        inputs[1].press_keys(PASSWORD)
        return {"mode": mode, "user_index": 0, "password_index": 1}

    if mode == "js":
        user_idx, pwd_idx = pick_login_input_indices(inputs)
        if user_idx is None or pwd_idx is None:
            raise RuntimeError(f"无法识别账号密码输入框: {inputs}")
        # CDP evaluate 不支持 arguments，用 JSON 注入
        payload = json.dumps({"u": user_idx, "p": pwd_idx, "user": USERNAME, "pwd": PASSWORD}, ensure_ascii=False)
        ok = sb.execute_script(
            r"""
            (() => {
              const data = %s;
              const inputs = Array.from(document.querySelectorAll('input'));
              function setValue(index, value) {
                const el = inputs[index];
                if (!el) return false;
                el.focus();
                const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                setter.call(el, value);
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.blur();
                return true;
              }
              return setValue(data.u, data.user) && setValue(data.p, data.pwd);
            })()
            """
            % payload
        )
        if not ok:
            raise RuntimeError(f"JS 填写输入框失败: user={user_idx}, pwd={pwd_idx}")
        sb.sleep(0.8)
        return {"mode": mode, "user_index": user_idx, "password_index": pwd_idx}

    raise RuntimeError(f"登录页输入框不足: {login_probe.get('state')}")


def click_login_button(sb):
    button_texts = ["登录", "登入", "Sign in", "Login", "提交"]
    try:
        buttons = sb.cdp.find_elements("button")
        for btn in buttons:
            try:
                txt = (btn.text or "").strip()
            except Exception:
                txt = ""
            if any(t.lower() in txt.lower() for t in button_texts) and "谷歌" not in txt and "无密码" not in txt:
                btn.click()
                return {"mode": "cdp", "text": txt}
    except Exception:
        pass

    for text in button_texts:
        try:
            selector = f'button:contains("{text}")'
            if sb.is_element_present(selector):
                sb.click(selector)
                return {"mode": "selenium", "text": text}
        except Exception:
            pass

    clicked = sb.execute_script(
        r"""
        (() => {
          const texts = ['登录', '登入', 'Sign in', 'Login', '提交'];
          const candidates = Array.from(document.querySelectorAll('button, [role="button"], input[type="submit"]'));
          const target = candidates.find((el) => {
            const text = (el.innerText || el.value || el.textContent || '').trim();
            if (text.includes('谷歌') || text.includes('无密码')) return false;
            return texts.some((t) => text.toLowerCase().includes(t.toLowerCase()));
          }) || candidates.find((el) => el.type === 'submit') || null;
          if (!target) return null;
          const text = (target.innerText || target.value || target.textContent || '').trim();
          target.click();
          return text || 'clicked';
        })()
        """
    )
    if clicked:
        return {"mode": "js", "text": clicked}
    return None


def click_signin_button(sb):
    try:
        buttons = sb.cdp.find_elements("button")
        for btn in buttons:
            try:
                txt = (btn.text or "").strip()
            except Exception:
                txt = ""
            if txt == "立即签到":
                btn.scroll_into_view()
                sb.sleep(0.8)
                btn.click()
                return True
    except Exception:
        pass
    return False


def verify_signed(sb, allow_refresh=True):
    if allow_refresh:
        sb.open(DASHBOARD_URL)
        sb.sleep(4)
    verify = body_text(sb)
    # 有「立即签到」且没有成功文案 → 未签
    if "立即签到" in verify and not has_success(verify):
        return False, verify
    if has_success(verify):
        return True, verify
    if "今天还没有签到" in verify:
        return False, verify
    return has_success(verify), verify


def ensure_login(sb):
    sb.activate_cdp_mode(LOGIN_URL)
    sb.sleep(3)
    if "/login" not in sb.get_current_url():
        return {"skipped": True, "url": sb.get_current_url()}

    # 首屏可能是 CF interstitial，先多等一会再探测输入框
    login_timeout = int(os.environ.get("HOHAI_LOGIN_TIMEOUT", "50"))
    login_probe = wait_for_login_inputs(sb, timeout=login_timeout)
    if login_probe.get("state", {}).get("left_login"):
        return {"skipped": True, "url": sb.get_current_url(), "probe": login_probe.get("state")}
    if not login_probe.get("mode"):
        # 最后再硬刷新一次并短等
        try:
            sb.open(LOGIN_URL)
            sb.sleep(3)
            login_probe = wait_for_login_inputs(sb, timeout=min(25, login_timeout))
        except Exception as e:
            login_probe = {"mode": None, "inputs": [], "state": {"final_open_error": str(e)}}
        if login_probe.get("state", {}).get("left_login"):
            return {"skipped": True, "url": sb.get_current_url(), "probe": login_probe.get("state")}
    if not login_probe.get("mode"):
        return {
            "ok": False,
            "reason": "登录页输入框不足",
            "state": login_probe.get("state"),
            "url": sb.get_current_url(),
            "hint": "可能被 Cloudflare 拦截或代理页面未加载完成",
        }
    try:
        fill_state = fill_login_inputs(sb, login_probe)
    except Exception as e:
        return {
            "ok": False,
            "reason": "登录页输入框填写失败",
            "error": str(e),
            "state": login_probe.get("state"),
            "url": sb.get_current_url(),
        }
    clicked = click_login_button(sb)
    if not clicked:
        return {
            "ok": False,
            "reason": "未找到登录按钮",
            "fill": fill_state,
            "state": login_probe.get("state"),
            "url": sb.get_current_url(),
        }
    # 登录后等跳转 dashboard
    for _ in range(16):
        sb.sleep(1)
        if "/login" not in sb.get_current_url():
            break
    return {
        "ok": True,
        "fill": fill_state,
        "clicked": clicked,
        "url": sb.get_current_url(),
        "probe": login_probe.get("state"),
    }


def do_checkin_flow(sb, proxy_label):
    step = {"proxy": proxy_label, "has_modal": False, "retries": [], "checkin": None}

    if OBSERVE_MODE and "/dashboard" in sb.get_current_url():
        sb.sleep(4)
    else:
        sb.open(DASHBOARD_URL)
        sb.sleep(4)

    if "/login" in sb.get_current_url():
        return False, "访问 dashboard 时回登录页", step

    install_network_hook(sb)

    body = body_text(sb)
    if has_success(body) and "立即签到" not in body:
        step["already"] = True
        return True, "页面已显示已签到", step

    if not click_signin_button(sb):
        # 可能已签到但文案不同
        ok, _ = verify_signed(sb, allow_refresh=True)
        if ok:
            return True, "刷新后确认已签到", step
        return False, "未找到立即签到按钮", step

    sb.sleep(1.5)
    probe = wait_modal_and_widget(sb, timeout=14)
    step["has_modal"] = bool(probe.get("modal_seen"))
    step["initial_probe"] = probe

    max_attempts = 4
    for i in range(1, max_attempts + 1):
        retry = {"attempt": i}
        body_now = body_text(sb)

        # 验证失败或弹窗消失 → 重新点立即签到
        need_reopen = (
            failure_seen(body_now)
            or (not modal_ready(body_now) and not get_gui_target(sb).get("selector"))
            or i > 1
        )
        if need_reopen and not modal_ready(body_now):
            retry["reopen"] = click_signin_button(sb)
            sb.sleep(1.5)

        retry["probe_before"] = wait_modal_and_widget(sb, timeout=10)
        retry["failure_seen_before_click"] = bool(retry["probe_before"].get("failure_seen"))
        retry["ready_before_click"] = bool(
            retry["probe_before"].get("modal_seen")
            and retry["probe_before"].get("widget_ready")
            and retry["probe_before"].get("geometry_ready")
            and retry["probe_before"].get("interactive_ready")
            and (
                retry["failure_seen_before_click"]
                or retry["probe_before"].get("stable_hits", 0) >= 3
                or rect_is_usable((retry["probe_before"].get("gui_target") or {}).get("rect"))
            )
        )
        retry["wait_before_click_s"] = 2.5 + i * 0.5
        sb.sleep(retry["wait_before_click_s"])
        retry["state_before_click"] = get_turnstile_state(sb)
        retry["gui_target_before_click"] = get_gui_target(sb)

        state_before = retry["state_before_click"]
        gui_before = retry["gui_target_before_click"]
        retry["stable_before_click"] = bool(
            retry["ready_before_click"]
            and rect_is_usable((gui_before or {}).get("rect") or state_before.get("widget_rect"))
        )

        if retry["stable_before_click"]:
            try:
                retry["visible_click"] = visible_click_turnstile(sb)
                retry["captcha_action_invoked"] = bool(retry["visible_click"].get("clicked"))
            except Exception as e:
                retry["captcha_action_invoked"] = False
                retry["error"] = str(e)
                # 回退 SeleniumBase 原生
                try:
                    if hasattr(sb, "uc_gui_click_cf"):
                        sb.uc_gui_click_cf()
                        retry["fallback"] = "uc_gui_click_cf"
                        retry["captcha_action_invoked"] = True
                except Exception as e2:
                    retry["fallback_error"] = str(e2)
        else:
            retry["captcha_action_invoked"] = False
            retry["skipped_reason"] = "small_box_not_ready"
            # 仍尝试原生
            try:
                if hasattr(sb, "uc_gui_click_cf"):
                    sb.uc_gui_click_cf()
                    retry["fallback"] = "uc_gui_click_cf_unready"
                    retry["captcha_action_invoked"] = True
            except Exception:
                pass

        # 等 token + checkin API（关键：不要过早刷新）
        checkin_entry = None
        for wait_i in range(18):
            sb.sleep(1)
            st = get_turnstile_state(sb)
            body_after = body_text(sb)
            entry = latest_checkin(sb)
            if entry:
                checkin_entry = entry
                ok_api, parsed = parse_checkin_ok(entry)
                retry["checkin"] = parsed
                retry["token_after_click"] = token_len_from_state(st)
                retry["body_hit"] = [x for x in MODAL_TEXTS + SUCCESS_TEXTS + FAILURE_TEXTS if x in body_after]
                if ok_api:
                    step["retries"].append(retry)
                    step["checkin"] = parsed
                    return True, "API 确认签到成功", step
                # 400 人机失败 → 下一轮
                if entry.get("status") == 400 or failure_seen(body_after):
                    retry["api_fail"] = parsed
                    break
            if has_success(body_after) and "立即签到" not in body_after:
                step["retries"].append(retry)
                return True, "页面显示已签到", step
            if token_len_from_state(st) > 20 and wait_i >= 2 and not entry:
                # token 有了但 API 还没发，继续等
                continue
            if failure_seen(body_after) and wait_i >= 3 and not entry:
                break

        retry["state_after_click"] = get_turnstile_state(sb)
        retry["gui_target_after_click"] = get_gui_target(sb)
        body_after = body_text(sb)
        retry["body_hit"] = [x for x in MODAL_TEXTS + SUCCESS_TEXTS + FAILURE_TEXTS if x in body_after]
        retry["token_after_click"] = token_len_from_state(retry["state_after_click"])
        retry["captcha_token_verified"] = has_verified_token(retry["token_after_click"])
        if checkin_entry and "checkin" not in retry:
            _, parsed = parse_checkin_ok(checkin_entry)
            retry["checkin"] = parsed
        step["retries"].append(retry)

        if has_success(body_after) and "立即签到" not in body_after:
            return True, "页面显示已签到", step

    # 最终刷新确认
    ok, _ = verify_signed(sb, allow_refresh=not OBSERVE_MODE)
    if ok:
        return True, "刷新后确认已签到", step

    if OBSERVE_MODE:
        hold_browser_for_observation(sb, "HOHAI 最终未确认签到成功，保留现场供观察", step)

    # 汇总 API 失败信息
    last_checkin = None
    for r in reversed(step.get("retries") or []):
        if r.get("checkin") or r.get("api_fail"):
            last_checkin = r.get("checkin") or r.get("api_fail")
            break
    step["checkin"] = last_checkin
    msg = "未找到签到成功证据"
    if last_checkin and isinstance(last_checkin, dict):
        m = last_checkin.get("message") or last_checkin.get("resp")
        if m:
            msg = f"签到未成功：{m}" if not isinstance(m, dict) else f"签到未成功：{m.get('message') or m}"
    return False, msg, step


def run_with_proxy(proxy):
    kwargs = dict(
        uc=True,
        test=False,
        locale_code="zh-CN",
        user_data_dir=PROFILE_DIR,
        xvfb=False,
        headed=HEADED,
    )
    if proxy:
        kwargs["proxy"] = proxy

    label = proxy or "direct"
    print(json.dumps({"event": "start", "proxy": label, "time": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False))

    with SB(**kwargs) as sb:
        login = ensure_login(sb)
        if login.get("ok") is False:
            return False, login.get("reason") or "登录失败", {"proxy": label, "login": login, "url": login.get("url")}

        # 登录后进 dashboard 再签
        if "/login" in sb.get_current_url():
            # 再等一会 cookie
            sb.sleep(3)
            sb.open(DASHBOARD_URL)
            sb.sleep(4)
            if "/login" in sb.get_current_url():
                return False, "访问 dashboard 时回登录页", {"proxy": label, "url": sb.get_current_url(), "login": login}

        ok, message, step = do_checkin_flow(sb, label)
        step["proxy"] = label
        step["url"] = sb.get_current_url()
        if not ok and OBSERVE_MODE:
            hold_browser_for_observation(sb, message, step)
        return ok, message, step


def main():
    if not USERNAME or not PASSWORD:
        done(False, "缺少 HOHAI_USERNAME / HOHAI_PASSWORD 环境变量")

    proxies = proxy_list_from_env()
    attempts = []
    last_fail = ("未执行", {})
    print(json.dumps({"event": "begin", "proxy_count": len(proxies), "api": env_bool("HOHAI_PROXY_API", True)}, ensure_ascii=False))

    for proxy in proxies:
        try:
            ok, message, step = run_with_proxy(proxy)
            attempts.append({"proxy": proxy or "direct", "ok": ok, "message": message, "checkin": (step or {}).get("checkin")})
            if ok:
                done(True, message, proxy=proxy or "direct", url=(step or {}).get("url"), checkin=(step or {}).get("checkin"), step=step, attempts=attempts)
            last_fail = (message, step or {})
            # 直连/代理失败后继续下一个
            print(json.dumps({"event": "proxy_failed", "proxy": proxy or "direct", "message": message}, ensure_ascii=False))
        except SystemExit:
            raise
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            attempts.append({"proxy": proxy or "direct", "ok": False, "error": err})
            last_fail = (f"浏览器启动或流程异常: {err}", {"error": err, "traceback": traceback.format_exc()[-1200:]})
            print(json.dumps({"event": "proxy_exception", "proxy": proxy or "direct", "error": err}, ensure_ascii=False))

    msg, extra = last_fail
    extra = dict(extra or {})
    extra["attempts"] = attempts
    extra["proxy"] = (attempts[-1]["proxy"] if attempts else "direct")
    done(False, msg, **extra)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        done(
            False,
            "浏览器启动或脚本顶层异常",
            error="%s: %s" % (type(e).__name__, str(e)),
            traceback=traceback.format_exc(),
        )
