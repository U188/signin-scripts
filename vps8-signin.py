#!/root/.openclaw/venvs/seleniumbase/bin/python
# -*- coding: utf-8 -*-
"""VPS8 (vps8.zz.cd) 每日签到 — NodeLoc OAuth + reCAPTCHA v2。

流程（实测必需）：
  1. 先完整登录 NodeLoc（www.nodeloc.com）
  2. 再访问 vps8 的 /nodeloc/login 完成 OAuth
  3. 打开 /points/signin，过 reCAPTCHA 后 POST /api/client/points/signin

环境变量（建议 source /root/.config/vps8-signin.env）：
  NODELOC_USERNAME / NODELOC_PASSWORD   必填
  VPS8_BASE                             默认 https://vps8.zz.cd
  VPS8_SB_PROFILE                       默认 /root/.config/seleniumbase-vps8
  VPS8_HEADED                           默认 1
  VPS8_PROXY                            可选，SeleniumBase 格式 user:pass@host:port
  YESCAPTCHA_API_KEY                    可选，YesCaptcha clientKey（打 reCAPTCHA v2）
  YESCAPTCHA_ENDPOINT                   可选，默认 https://api.yescaptcha.com
  SIGNIN_TG_BOT_TOKEN / SIGNIN_TG_CHAT_ID  通知

退出码：
  0 签到成功 / 今日已签到
  2 登录失败
  3 CAPTCHA 未过（缺打码 key 或打码失败）
  4 签到 API 失败
  1 其它错误
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from seleniumbase import SB

BASE = os.environ.get("VPS8_BASE", "https://vps8.zz.cd").rstrip("/")
NODELOC = os.environ.get("NODELOC_BASE", "https://www.nodeloc.com").rstrip("/")
USERNAME = os.environ.get("NODELOC_USERNAME", "").strip()
PASSWORD = os.environ.get("NODELOC_PASSWORD", "").strip()
PROFILE_DIR = os.environ.get("VPS8_SB_PROFILE", "/root/.config/seleniumbase-vps8")
HEADED = os.environ.get("VPS8_HEADED", "1") == "1"
DEBUG_DIR = Path(os.environ.get("VPS8_DEBUG_DIR", "/tmp/vps8-debug"))
SITEKEY_DEFAULT = "6LemX2YsAAAAAHtenbdCpRE_3qj83yzhTM4-Jvit"

TELEGRAM_BOT_TOKEN = (
    os.environ.get("SIGNIN_TG_BOT_TOKEN")
    or os.environ.get("TELEGRAM_BOT_TOKEN")
    or ""
).strip()
TELEGRAM_CHAT_ID = (
    os.environ.get("SIGNIN_TG_CHAT_ID")
    or (os.environ.get("TELEGRAM_ALLOWED_USERS", "").split(",")[0].strip())
    or ""
).strip()

YESCAPTCHA_KEY = (
    os.environ.get("YESCAPTCHA_API_KEY")
    or os.environ.get("GROK2API_YESCAPTCHA_KEY")
    or ""
).strip()
YESCAPTCHA_ENDPOINT = (
    os.environ.get("YESCAPTCHA_ENDPOINT")
    or os.environ.get("GROK2API_YESCAPTCHA_ENDPOINT")
    or os.environ.get("YESCAPTCHA_API_BASE")
    or "https://api.yescaptcha.com"
).rstrip("/")


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def normalize_proxy(raw: str | None) -> str | None:
    if not raw:
        return None
    p = raw.strip()
    for prefix in ("socks5h://", "socks5://", "http://", "https://"):
        if p.lower().startswith(prefix):
            p = p[len(prefix) :]
            break
    return p or None


def send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        data = urllib.parse.urlencode(
            {"chat_id": TELEGRAM_CHAT_ID, "text": text}
        ).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data=data,
            method="POST",
        )
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:
        print(f"[notify] Telegram 失败: {e}", file=sys.stderr)


def js(sb, code: str):
    """CDP-safe IIFE evaluate."""
    if not code.strip().startswith("("):
        code = f"(() => {{ {code} }})()"
    return sb.execute_script(code)


def body_text(sb) -> str:
    return js(sb, 'return (document.body && document.body.innerText) || "";') or ""


def snap(sb, name: str) -> None:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        sb.save_screenshot(str(DEBUG_DIR / f"{name}.png"))
        (DEBUG_DIR / f"{name}.html").write_text(
            sb.get_page_source() or "", errors="ignore"
        )
    except Exception as e:
        log(f"snap {name} fail: {e}")
    try:
        log(f"snap {name} url={sb.get_current_url()}")
    except Exception:
        pass


def yescaptcha_post(path: str, payload: dict, timeout: float = 45) -> dict:
    url = f"{YESCAPTCHA_ENDPOINT}{path}"
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def solve_recaptcha_v2(website_url: str, sitekey: str) -> str:
    """YesCaptcha NoCaptchaTaskProxyless → gRecaptchaResponse."""
    if not YESCAPTCHA_KEY:
        raise RuntimeError("YESCAPTCHA_API_KEY 未设置，无法自动过 reCAPTCHA 图片题")
    log(f"YesCaptcha createTask sitekey={sitekey[:12]}...")
    created = yescaptcha_post(
        "/createTask",
        {
            "clientKey": YESCAPTCHA_KEY,
            "task": {
                "type": "NoCaptchaTaskProxyless",
                "websiteURL": website_url,
                "websiteKey": sitekey,
            },
        },
    )
    if created.get("errorId"):
        raise RuntimeError(f"YesCaptcha createTask: {created}")
    task_id = created.get("taskId")
    if not task_id:
        raise RuntimeError(f"YesCaptcha no taskId: {created}")
    log(f"YesCaptcha taskId={task_id}, polling...")
    deadline = time.time() + float(os.environ.get("YESCAPTCHA_TIMEOUT", "180"))
    while time.time() < deadline:
        time.sleep(3)
        data = yescaptcha_post(
            "/getTaskResult",
            {"clientKey": YESCAPTCHA_KEY, "taskId": task_id},
        )
        if data.get("errorId"):
            raise RuntimeError(f"YesCaptcha getTaskResult: {data}")
        if data.get("status") == "ready":
            sol = data.get("solution") or {}
            token = (
                sol.get("gRecaptchaResponse")
                or sol.get("token")
                or sol.get("text")
                or ""
            )
            if not token:
                raise RuntimeError(f"YesCaptcha empty solution: {data}")
            log(f"YesCaptcha ok token_len={len(token)}")
            return token
        log(f"YesCaptcha status={data.get('status')}")
    raise TimeoutError("YesCaptcha timeout waiting for reCAPTCHA")


def inject_recaptcha_token(sb, token: str) -> None:
    js(
        sb,
        r"""
      const token = %s;
      let ta = document.querySelector('#g-recaptcha-response, textarea[name="g-recaptcha-response"]');
      if (!ta) {
        ta = document.createElement('textarea');
        ta.id = 'g-recaptcha-response';
        ta.name = 'g-recaptcha-response';
        ta.style.display = 'none';
        (document.querySelector('#points-signin-form') || document.body).appendChild(ta);
      }
      ta.value = token;
      ta.innerHTML = token;
      try {
        if (window.grecaptcha && window.grecaptcha.getResponse) {
          // no-op: token already in textarea for form POST
        }
      } catch (e) {}
      return (ta.value || '').length;
    """
        % json.dumps(token),
    )


def nodeloc_is_logged_in(sb) -> bool:
    """Strict check — nav items like 我的帖子 appear even when logged out."""
    t = body_text(sb)
    if "欢迎回来" in t:
        return True
    # username chip / current-user in discourse
    if js(
        sb,
        r"""
      return !!(
        document.querySelector('#current-user, .current-user, button.icon.btn-flat') ||
        document.querySelector('img.avatar') && document.querySelector('.user-menu, .user-menu-trigger, #quick-access-profile')
      );
    """,
    ):
        return True
    # explicit logout control
    if js(
        sb,
        r"""
      const a = [...document.querySelectorAll('a,button')].some(el => {
        const s = ((el.innerText||'') + (el.getAttribute('href')||'') + (el.getAttribute('title')||'')).toLowerCase();
        return s.includes('logout') || s.includes('log out') || s.includes('退出登录') || s.includes('/session/csrf');
      });
      // discourse logged-in often has #current-user
      return a;
    """,
    ):
        return True
    return False


def ensure_nodeloc_login(sb) -> bool:
    sb.open(f"{NODELOC}/")
    time.sleep(2)
    if nodeloc_is_logged_in(sb):
        log("NodeLoc 已登录")
        return True
    log("NodeLoc 登录中...")
    sb.open(f"{NODELOC}/login")
    time.sleep(2)
    # already redirected home while logged in
    if not sb.is_element_present("#login-account-name"):
        if "login" not in sb.get_current_url() and nodeloc_is_logged_in(sb):
            log("NodeLoc 登录页跳转，已登录")
            return True
        # discourse may use different selectors
        if sb.is_element_present("#login-account-name") is False:
            # try alternate
            for sel in ['input[name="login"]', "#login-account-name", 'input[autocomplete="username"]']:
                if sb.is_element_present(sel):
                    break
            else:
                # force login URL with redirect
                sb.open(f"{NODELOC}/login")
                time.sleep(3)
    if not sb.is_element_present("#login-account-name"):
        # last chance: if cookies make OAuth work, allow continue
        log("NodeLoc 登录表单未出现，尝试依赖现有 cookie")
        snap(sb, "nl_no_form")
        sb.open(f"{NODELOC}/")
        time.sleep(1)
        return True  # OAuth path will verify
    sb.type("#login-account-name", USERNAME)
    sb.type("#login-account-password", PASSWORD)
    try:
        sb.click("#login-form .btn-primary")
    except Exception:
        try:
            sb.click("button.btn-primary")
        except Exception:
            js(sb, 'document.querySelector("#login-form button, button.btn-primary")?.click(); return 1;')
    time.sleep(4)
    ok = nodeloc_is_logged_in(sb) or (
        "login" not in sb.get_current_url() and USERNAME.lower() in body_text(sb).lower()
    )
    # soft ok: not on login page after submit
    if not ok and "login" not in sb.get_current_url():
        ok = True
    log(f"NodeLoc 登录结果 ok={ok} url={sb.get_current_url()}")
    if not ok:
        snap(sb, "nl_login_fail")
    return ok


def ensure_vps8_session(sb, retries: int = 3) -> bool:
    sb.open(f"{BASE}/dashboard")
    time.sleep(3)
    if "/login" not in sb.get_current_url() and (
        "签到" in body_text(sb) or "客户区域" in (sb.get_title() or "")
    ):
        log("VPS8 会话有效")
        return True

    for attempt in range(1, retries + 1):
        log(f"VPS8 OAuth via NodeLoc (attempt {attempt}/{retries})...")
        # Prefer clicking the Nodeloc button on login page (more reliable than bare /nodeloc/login)
        sb.open(f"{BASE}/login")
        time.sleep(2)
        clicked = False
        try:
            if sb.is_element_present('a[href*="nodeloc/login"]'):
                sb.click('a[href*="nodeloc/login"]')
                clicked = True
            else:
                clicked = bool(
                    js(
                        sb,
                        r"""
                      const els = [...document.querySelectorAll('a,button')];
                      for (const el of els) {
                        const s = (el.innerText||'') + (el.href||'');
                        if (/nodeloc/i.test(s)) { el.click(); return true; }
                      }
                      return false;
                    """,
                    )
                )
        except Exception as e:
            log(f"click Nodeloc btn: {e}")
        if not clicked:
            sb.open(f"{BASE}/nodeloc/login")

        err = None
        for i in range(50):
            time.sleep(1)
            u = sb.get_current_url()
            if "error=" in u:
                err = u
                log(f"OAuth error: {u}")
                break
            path = u.split("?", 1)[0]
            if "dashboard" in u or "points" in u:
                break
            if "vps8" in u and "login" not in path and "nodeloc" not in path and "callback" not in path:
                break
            if "callback" in u and i > 2:
                # wait for redirect after callback
                continue
        if err:
            snap(sb, f"oauth_error_{attempt}")
            # re-login NodeLoc then retry (server-side NL timeout is intermittent)
            if attempt < retries:
                log("OAuth 失败，刷新 NodeLoc 会话后重试")
                ensure_nodeloc_login(sb)
                time.sleep(2)
                continue
            return False

        sb.open(f"{BASE}/dashboard")
        time.sleep(3)
        if "/login" not in sb.get_current_url():
            log(f"VPS8 session ok url={sb.get_current_url()}")
            return True
        log(f"attempt {attempt} still on login")
        if attempt < retries:
            ensure_nodeloc_login(sb)

    snap(sb, "vps8_login_fail")
    return False


def page_status(sb) -> dict:
    t = body_text(sb)
    points = None
    m = None
    import re

    m = re.search(r"当前积分[：:]\s*(\d+)", t)
    if m:
        points = int(m.group(1))
    streak = None
    m = re.search(r"当前连续签到[：:]\s*(\d+)", t)
    if m:
        streak = int(m.group(1))
    already = any(x in t for x in ("今日签到状态：已签到", "今日已签到", "已签到"))
    unsigned = "今日签到状态：未签到" in t
    return {
        "points": points,
        "streak": streak,
        "already": already and not unsigned,
        "unsigned": unsigned,
        "text_snip": " | ".join(
            ln.strip()
            for ln in t.splitlines()
            if any(k in ln for k in ("积分", "签到", "验证", "CAPTCHA", "成功", "失败"))
        )[:500],
    }


def extract_sitekey(sb) -> str:
    sk = js(
        sb,
        r"""
      const el = document.querySelector('.g-recaptcha[data-sitekey], [data-sitekey]');
      return (el && el.getAttribute('data-sitekey')) || '';
    """,
    )
    return (sk or SITEKEY_DEFAULT).strip()


def extract_form_fields(sb) -> dict:
    return js(
        sb,
        r"""
      const form = document.querySelector('#points-signin-form') || document.querySelector('form.api-form');
      if (!form) return {};
      const out = {};
      for (const i of form.querySelectorAll('input,textarea')) {
        if (i.name) out[i.name] = i.value || '';
      }
      out.__action = form.action || '';
      return out;
    """,
    ) or {}


def hook_fetch(sb) -> None:
    js(
        sb,
        r"""
      window.__net = [];
      if (!window.__vps8_hook) {
        window.__vps8_hook = true;
        const ofetch = window.fetch.bind(window);
        window.fetch = async function(...args) {
          let url = args[0], method = 'GET', body = null;
          if (url && typeof url === 'object' && url.url) url = url.url;
          if (args[1]) { method = args[1].method || method; body = args[1].body; }
          try {
            const res = await ofetch(...args);
            let txt = '';
            try { txt = await res.clone().text(); } catch (e) {}
            window.__net.push({
              url: String(url), method, status: res.status,
              req: String(body || '').slice(0, 600),
              resp: txt.slice(0, 1000),
            });
            return res;
          } catch (e) {
            window.__net.push({url: String(url), method, error: String(e)});
            throw e;
          }
        };
      }
      return true;
    """,
    )


def try_gui_recaptcha(sb) -> bool:
    """Best-effort checkbox click; often still gets image challenge on DC IP."""
    try:
        js(
            sb,
            'document.querySelector("#points-signin-form")?.scrollIntoView({block:"center"}); return 1;',
        )
        time.sleep(0.8)
        if hasattr(sb, "uc_gui_click_rc"):
            sb.uc_gui_click_rc()
        elif hasattr(sb, "uc_gui_click_captcha"):
            sb.uc_gui_click_captcha()
        time.sleep(3)
        tlen = js(
            sb,
            r"""
          const g = document.querySelector('#g-recaptcha-response, [name="g-recaptcha-response"]');
          let gr = '';
          try { gr = window.grecaptcha && window.grecaptcha.getResponse() || ''; } catch (e) {}
          return Math.max((g && g.value || '').length, (gr || '').length);
        """,
        )
        log(f"GUI reCAPTCHA token_len={tlen}")
        return int(tlen or 0) > 20
    except Exception as e:
        log(f"GUI reCAPTCHA fail: {e}")
        return False


def submit_signin(sb, captcha_token: str | None = None) -> dict:
    if captcha_token:
        inject_recaptcha_token(sb, captcha_token)
    hook_fetch(sb)
    # Prefer form submit path used by site (fetch from api-form)
    try:
        sb.click("#points-signin-submit")
    except Exception:
        js(
            sb,
            r"""
          const b = document.querySelector('#points-signin-submit');
          if (b) b.click();
          else document.querySelector('#points-signin-form')?.requestSubmit?.();
          return true;
        """,
        )
    result = None
    for _ in range(20):
        time.sleep(0.5)
        net = js(sb, 'return (window.__net || []).filter(x => String(x.url).includes("signin"));')
        if net:
            result = net[-1]
            break
    # fallback: manual fetch with form fields
    if not result:
        fields = extract_form_fields(sb)
        if captcha_token:
            fields["g-recaptcha-response"] = captcha_token
        payload = {
            k: fields.get(k, "")
            for k in (
                "CSRFToken",
                "signin_nonce",
                "form_rendered_at",
                "g-recaptcha-response",
            )
            if k in fields or k == "g-recaptcha-response"
        }
        if captcha_token:
            payload["g-recaptcha-response"] = captcha_token
        result = js(
            sb,
            r"""
          const payload = %s;
          return fetch('/api/client/points/signin', {
            method: 'POST',
            credentials: 'include',
            headers: {
              'Content-Type': 'application/json',
              'Accept': 'application/json',
              'X-Requested-With': 'XMLHttpRequest',
            },
            body: JSON.stringify(payload),
          }).then(async r => {
            const txt = await r.text();
            return {url: '/api/client/points/signin', method: 'POST', status: r.status, req: JSON.stringify(payload).slice(0,400), resp: txt.slice(0,1000)};
          }).catch(e => ({error: String(e)}));
        """
            % json.dumps(payload, ensure_ascii=False),
        )
    return result or {}


def parse_api(result: dict) -> tuple[str, str]:
    """return (status, message) status in success|already|captcha|error"""
    if not result:
        return "error", "无 API 响应"
    if result.get("error") and not result.get("resp"):
        return "error", str(result.get("error"))
    raw = result.get("resp") or ""
    try:
        data = json.loads(raw)
    except Exception:
        return "error", raw[:200]
    err = data.get("error") or {}
    if err:
        code = err.get("code")
        msg = err.get("message") or str(err)
        if code == 9999 or "CAPTCHA" in str(msg).upper() or "验证码" in str(msg):
            return "captcha", msg
        if any(x in str(msg) for x in ("已签到", "今日已", "重复")):
            return "already", msg
        return "error", f"{msg} ({code})"
    # success shapes
    res = data.get("result")
    if res is not None or data.get("success") is True:
        return "success", json.dumps(data, ensure_ascii=False)[:300]
    if data.get("message"):
        msg = str(data["message"])
        if any(x in msg for x in ("成功", "已签到")):
            return "success" if "成功" in msg else "already", msg
    return "error", raw[:300]


def main() -> int:
    if not USERNAME or not PASSWORD:
        print("缺少 NODELOC_USERNAME / NODELOC_PASSWORD", file=sys.stderr)
        return 1
    os.environ.setdefault("DISPLAY", os.environ.get("DISPLAY", ":1"))
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    Path(PROFILE_DIR).mkdir(parents=True, exist_ok=True)

    proxy = normalize_proxy(
        os.environ.get("VPS8_PROXY")
        or os.environ.get("HOHAI_PROXY")
        or ""
    )
    # 默认不用代理：VPS8 NodeLoc OAuth 直连更稳；代理仅在显式 VPS8_PROXY 时启用
    if not os.environ.get("VPS8_PROXY"):
        proxy = None

    kwargs = dict(
        uc=True,
        headed=HEADED,
        user_data_dir=PROFILE_DIR,
        chromium_arg="--no-sandbox,--disable-dev-shm-usage",
    )
    if proxy:
        kwargs["proxy"] = proxy
        log(f"proxy={proxy.split('@')[-1] if '@' in proxy else proxy}")

    report = {
        "site": BASE,
        "user": USERNAME,
        "ok": False,
        "status": "unknown",
        "message": "",
    }

    try:
        with SB(**kwargs) as sb:
            if not ensure_nodeloc_login(sb):
                report.update(status="login_fail", message="NodeLoc 登录失败")
                send_telegram(f"❌ VPS8 签到：NodeLoc 登录失败 ({USERNAME})")
                return 2
            if not ensure_vps8_session(sb):
                report.update(
                    status="login_fail",
                    message="VPS8 OAuth 失败（可先登录 NodeLoc 再试）",
                )
                send_telegram(f"❌ VPS8 签到：OAuth 失败 ({USERNAME})")
                return 2

            sb.open(f"{BASE}/points/signin")
            time.sleep(4)
            snap(sb, "signin_page")
            st = page_status(sb)
            log(f"page status: {st}")
            if st.get("already") and not st.get("unsigned"):
                report.update(
                    ok=True,
                    status="already",
                    message=f"今日已签到 积分={st.get('points')} 连续={st.get('streak')}",
                )
                send_telegram(f"✅ VPS8 签到：今日已签到 | 积分 {st.get('points')} | 连续 {st.get('streak')} 天")
                return 0

            # wait recaptcha
            for _ in range(20):
                if js(
                    sb,
                    'return !!(window.grecaptcha && document.querySelector(".g-recaptcha, iframe[src*=\\"recaptcha\\"]"));',
                ):
                    break
                time.sleep(0.5)

            captcha_token = None
            # 1) GUI click (cheap; often insufficient — image challenge)
            if try_gui_recaptcha(sb):
                captcha_token = js(
                    sb,
                    r"""
                  const g = document.querySelector('#g-recaptcha-response, [name="g-recaptcha-response"]');
                  let gr = '';
                  try { gr = window.grecaptcha.getResponse() || ''; } catch (e) {}
                  return (g && g.value) || gr || '';
                """,
                )

            # 2) YesCaptcha if still no token
            if not captcha_token or len(str(captcha_token)) < 20:
                sitekey = extract_sitekey(sb)
                if not YESCAPTCHA_KEY:
                    snap(sb, "need_captcha")
                    msg = (
                        "需要 reCAPTCHA v2（图片题）。本机无 YESCAPTCHA_API_KEY，"
                        "GUI 点击只能弹出挑战无法解题。"
                        "请设置 YESCAPTCHA_API_KEY 后重跑。"
                    )
                    log(msg)
                    report.update(status="captcha", message=msg)
                    send_telegram(f"⚠️ VPS8 签到卡住：需要 YesCaptcha 打码 key\n账号 {USERNAME}\n积分 {st.get('points')} 未签到")
                    return 3
                try:
                    captcha_token = solve_recaptcha_v2(
                        f"{BASE}/points/signin", sitekey
                    )
                except Exception as e:
                    log(f"YesCaptcha 失败: {e}")
                    snap(sb, "yescaptcha_fail")
                    report.update(status="captcha", message=str(e))
                    send_telegram(f"❌ VPS8 签到：YesCaptcha 失败\n{e}")
                    return 3

            api = submit_signin(sb, captcha_token)
            log(f"API: {json.dumps(api, ensure_ascii=False)[:500]}")
            status, message = parse_api(api)
            time.sleep(2)
            sb.open(f"{BASE}/points/signin")
            time.sleep(3)
            st2 = page_status(sb)
            snap(sb, "after_signin")

            if status == "success" or st2.get("already"):
                report.update(
                    ok=True,
                    status="success" if status == "success" else "already",
                    message=f"{message} | 积分={st2.get('points')} 连续={st2.get('streak')}",
                )
                send_telegram(
                    f"✅ VPS8 签到成功\n积分 {st2.get('points')} | 连续 {st2.get('streak')} 天\n{message[:200]}"
                )
                return 0
            if status == "already":
                report.update(ok=True, status="already", message=message)
                send_telegram(f"✅ VPS8 今日已签到\n{message[:200]}")
                return 0
            if status == "captcha":
                report.update(status="captcha", message=message)
                send_telegram(f"⚠️ VPS8 签到 CAPTCHA 失败\n{message}")
                return 3
            report.update(status="error", message=message)
            send_telegram(f"❌ VPS8 签到失败\n{message[:300]}")
            return 4
    except Exception as e:
        traceback.print_exc()
        report.update(status="error", message=str(e))
        send_telegram(f"❌ VPS8 签到异常\n{e}")
        return 1
    finally:
        log(f"report={json.dumps(report, ensure_ascii=False)}")


if __name__ == "__main__":
    sys.exit(main())
