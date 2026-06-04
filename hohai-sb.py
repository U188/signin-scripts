#!/root/.openclaw/venvs/seleniumbase/bin/python
# -*- coding: utf-8 -*-

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
USERNAME = os.environ.get("HOHAI_USERNAME", "")
PASSWORD = os.environ.get("HOHAI_PASSWORD", "")
PROFILE_DIR = os.environ.get("HOHAI_SB_PROFILE", "/root/.config/seleniumbase-hohai")
HEADED = os.environ.get("HOHAI_HEADED", "1") == "1"
KEEP_OPEN_ON_FAIL = os.environ.get("HOHAI_KEEP_OPEN_ON_FAIL", "0") == "1"
OBSERVE_MODE = os.environ.get("HOHAI_OBSERVE_MODE", "0") == "1"
HOLD_OPEN_SECS = int(os.environ.get("HOHAI_HOLD_OPEN_SECS", "600"))
TELEGRAM_BOT_TOKEN = os.environ.get("SIGNIN_TG_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("SIGNIN_TG_CHAT_ID", "")

SUCCESS_TEXTS = ['已签到', '今日已签到', '签到成功']
MODAL_TEXTS = ['签到验证', '请完成人机验证以继续签到']
FAILURE_TEXTS = ['Turnstile验证失败', 'Validation failed', '验证失败']
GUI_TARGET_SELECTORS = [
    'iframe[src*="challenge-platform"]',
    'iframe[src*="turnstile"]',
    '.turnstile-widget',
    '.cloudflare-turnstile-container',
    '[id^="verification-checkin-"]',
]


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


def format_report(ok, message, extra):
    status = "✅ 签到完成" if ok else "❌ 签到失败"
    lines = [
        "HOHAI 自动签到 执行结果：" + status,
        "结果明细：",
    ]
    if ok:
        lines.append(" • ✅ 浏览器签到流程已跑通")
        lines.append(f" • ✅ 最终结果：{message}")
    else:
        lines.append(f" • ❌ 失败原因：{message}")
        if extra.get("error"):
            lines.append(f" • ⚠️ 原始错误：{str(extra.get('error'))[:500]}")
    if extra.get("url"):
        lines.append(f" • ✅ 页面位置：{extra.get('url')}" if ok else f" • 页面位置：{extra.get('url')}")
    if extra.get("state"):
        lines.append(f" • 调试状态：{json.dumps(extra.get('state'), ensure_ascii=False)[:500]}")
    lines.append(f"执行时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    return "\n".join(lines)


def done(ok, message, **extra):
    data = {"ok": ok, "message": message}
    data.update(extra)
    report = format_report(ok, message, extra)
    send_telegram(report)
    print(report)
    print(json.dumps(data, ensure_ascii=False))
    sys.exit(0 if ok else 1)


def body_text(sb):
    try:
        return sb.get_text('body')
    except Exception:
        return ''


def has_success(text):
    return any(x in text for x in SUCCESS_TEXTS)


def modal_ready(text):
    return all(x in text for x in MODAL_TEXTS)


def failure_seen(text):
    return any(x in text for x in FAILURE_TEXTS)


def token_len_from_state(state):
    return int((state or {}).get('token_len', 0) or 0)


def has_verified_token(token_len):
    return token_len > 20


def placeholder_only_state(state):
    html = re.sub(r'\s+', ' ', str((state or {}).get('widget_html', '') or '')).lower()
    if not html:
        return True
    if any(tag in html for tag in ('<iframe', '<canvas', '<svg', 'checkbox', 'challenge')):
        return False
    return ('cf-turnstile-response' in html) and (html.count('<input') == 1) and (html.count('<div') <= 2)


def visual_checkbox_ready(rect):
    if not HEADED or not rect_is_usable(rect):
        return {'ready': False, 'reason': 'not_headed_or_bad_rect'}
    # 只要 rect 可用就认为 ready，不再依赖像素分析
    # Turnstile 渲染出 checkbox 后外观多变，像素判断误判率太高
    return {'ready': True, 'reason': 'rect_usable_bypass'}


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
        return {'error': str(e)}


def rect_is_usable(rect):
    return bool(rect and rect.get('width', 0) >= 240 and rect.get('height', 0) >= 60)


def rect_is_stable(prev_rect, rect, tolerance=4):
    if not prev_rect or not rect:
        return False
    return (
        abs(prev_rect.get('x', 0) - rect.get('x', 0)) <= tolerance
        and abs(prev_rect.get('y', 0) - rect.get('y', 0)) <= tolerance
        and abs(prev_rect.get('width', 0) - rect.get('width', 0)) <= tolerance
        and abs(prev_rect.get('height', 0) - rect.get('height', 0)) <= tolerance
    )


def get_gui_target(sb):
    for selector in GUI_TARGET_SELECTORS:
        try:
            if sb.is_element_present(selector):
                rect = sb.get_gui_element_rect(selector)
                if rect_is_usable(rect):
                    return {'selector': selector, 'rect': rect}
        except Exception:
            continue
    return {'selector': None, 'rect': None}


def wait_modal_and_widget(sb, timeout=12):
    rounds = int(timeout / 0.5)
    last = {'modal_seen': False, 'widget_ready': False, 'state': {}, 'body_hit': [], 'failure_seen': False, 'stable_hits': 0}
    stable_hits = 0
    prev_rect = None
    for _ in range(rounds):
        text = body_text(sb)
        state = get_turnstile_state(sb)
        gui_target = get_gui_target(sb)
        modal_seen_now = modal_ready(text)
        failure_seen_now = failure_seen(text)
        rect = gui_target.get('rect') or state.get('widget_rect')
        widget_ready = bool(
            state.get('has_widget')
            or state.get('has_iframe')
            or state.get('has_token_input')
            or gui_target.get('selector')
        )
        center_ready = state.get('center_hit_in_widget', False) or bool(gui_target.get('selector'))
        geometry_ready = rect_is_usable(rect)
        placeholder_only = placeholder_only_state(state)
        visual_probe = visual_checkbox_ready(rect) if geometry_ready else {'ready': False, 'reason': 'bad_rect'}
        interactive_ready = bool(state.get('has_iframe')) or visual_probe.get('ready', False) or not placeholder_only or geometry_ready
        current_stable = modal_seen_now and widget_ready and geometry_ready and interactive_ready
        if current_stable:
            stable_hits = stable_hits + 1 if rect_is_stable(prev_rect, rect) else 1
        else:
            stable_hits = 0
        last = {
            'modal_seen': modal_seen_now,
            'failure_seen': failure_seen_now,
            'widget_ready': widget_ready,
            'geometry_ready': geometry_ready,
            'center_ready': center_ready,
            'interactive_ready': interactive_ready,
            'placeholder_only': placeholder_only,
            'visual_probe': visual_probe,
            'stable_hits': stable_hits,
            'gui_target': gui_target,
            'state': state,
            'body_hit': [x for x in MODAL_TEXTS + FAILURE_TEXTS if x in text],
        }
        if current_stable and ((failure_seen_now and stable_hits >= 2) or stable_hits >= 4):
            return last
        prev_rect = rect
        sb.sleep(0.5)
    return last


def hold_browser_for_observation(sb, reason, step):
    if not (KEEP_OPEN_ON_FAIL and HEADED):
        return
    print(json.dumps({
        "ok": False,
        "message": reason,
        "url": sb.get_current_url(),
        "step": step,
        "observe_mode": OBSERVE_MODE,
        "hold_open_secs": HOLD_OPEN_SECS,
    }, ensure_ascii=False))
    sb.sleep(HOLD_OPEN_SECS)


def visible_click_turnstile(sb):
    import pyautogui

    target = get_gui_target(sb)
    selector = target.get('selector')
    rect = target.get('rect')
    if not selector or not rect_is_usable(rect):
        return {'clicked': False, 'reason': 'no_stable_gui_target'}

    left_bias_x = rect['x'] + (rect['width'] * 0.12)
    center_y = rect['y'] + (rect['height'] * 0.5)
    click_x = int(max(rect['x'] + 8, min(left_bias_x + random.randint(-6, 6), rect['x'] + rect['width'] - 8)))
    click_y = int(max(rect['y'] + 8, min(center_y + random.randint(-4, 4), rect['y'] + rect['height'] - 8)))

    pyautogui.moveTo(click_x, click_y, duration=0.35)
    sb.sleep(0.45)
    pyautogui.mouseDown(x=click_x, y=click_y)
    sb.sleep(0.18)
    pyautogui.mouseUp(x=click_x, y=click_y)
    return {
        'clicked': True,
        'selector': selector,
        'rect': rect,
        'point': {'x': click_x, 'y': click_y},
        'target_area': 'left_checkbox_bias',
    }




def wait_for_login_inputs(sb, timeout=18):
    """Return two usable login inputs with multiple fallbacks.

    HOHAI sometimes renders the login page in a way where sb.cdp.find_elements('input')
    returns 0 even though the DOM contains inputs.  Do not fail on the first lookup.
    """
    deadline = time.time() + timeout
    last = {'cdp_count': 0, 'selenium_count': 0, 'js_count': 0, 'url': ''}
    while time.time() < deadline:
        last['url'] = sb.get_current_url()

        try:
            inputs = sb.cdp.find_elements('input')
            last['cdp_count'] = len(inputs)
            if len(inputs) >= 2:
                return {'mode': 'cdp', 'inputs': inputs, 'state': last}
        except Exception as e:
            last['cdp_error'] = str(e)[:160]

        try:
            inputs = sb.find_elements('input')
            last['selenium_count'] = len(inputs)
            if len(inputs) >= 2:
                return {'mode': 'selenium', 'inputs': inputs, 'state': last}
        except Exception as e:
            last['selenium_error'] = str(e)[:160]

        try:
            js_inputs = sb.execute_script(r"""
                return Array.from(document.querySelectorAll('input')).map((el, index) => ({
                    index,
                    type: el.type || '',
                    name: el.name || '',
                    id: el.id || '',
                    placeholder: el.placeholder || '',
                    autocomplete: el.autocomplete || '',
                    visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
                }));
            """) or []
            last['js_count'] = len(js_inputs)
            if len(js_inputs) >= 2:
                return {'mode': 'js', 'inputs': js_inputs, 'state': last}
        except Exception as e:
            last['js_error'] = str(e)[:160]

        sb.sleep(0.75)
    return {'mode': None, 'inputs': [], 'state': last}


def pick_login_input_indices(js_inputs):
    user_idx = None
    pwd_idx = None
    for item in js_inputs:
        text = ' '.join(str(item.get(k, '')) for k in ['type', 'name', 'id', 'placeholder', 'autocomplete']).lower()
        if pwd_idx is None and ('password' in text or item.get('type') == 'password'):
            pwd_idx = item.get('index')
        if user_idx is None and any(k in text for k in ['user', 'email', 'mail', 'account', 'phone', 'name', '登录', '账号', '邮箱', '手机号']):
            user_idx = item.get('index')
    visible = [item.get('index') for item in js_inputs if item.get('visible')]
    ordered = visible or [item.get('index') for item in js_inputs]
    if user_idx is None and ordered:
        user_idx = ordered[0]
    if pwd_idx is None:
        for idx in ordered:
            if idx != user_idx:
                pwd_idx = idx
                break
    return user_idx, pwd_idx


def fill_login_inputs(sb, login_probe):
    mode = login_probe.get('mode')
    inputs = login_probe.get('inputs') or []
    if mode in ('cdp', 'selenium'):
        inputs[0].click()
        sb.sleep(0.4)
        inputs[0].press_keys(USERNAME)
        sb.sleep(0.4)
        inputs[1].click()
        sb.sleep(0.4)
        inputs[1].press_keys(PASSWORD)
        return {'mode': mode, 'user_index': 0, 'password_index': 1}

    if mode == 'js':
        user_idx, pwd_idx = pick_login_input_indices(inputs)
        if user_idx is None or pwd_idx is None:
            raise RuntimeError(f'无法识别账号密码输入框: {inputs}')
        ok = sb.execute_script(r"""
            const [userIndex, pwdIndex, username, password] = arguments;
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
            return setValue(userIndex, username) && setValue(pwdIndex, password);
        """, user_idx, pwd_idx, USERNAME, PASSWORD)
        if not ok:
            raise RuntimeError(f'JS 填写输入框失败: user={user_idx}, pwd={pwd_idx}')
        sb.sleep(0.8)
        return {'mode': mode, 'user_index': user_idx, 'password_index': pwd_idx}

    raise RuntimeError(f'登录页输入框不足: {login_probe.get("state")}')


def click_login_button(sb):
    button_texts = ['登录', '登入', 'Sign in', 'Login', '提交']
    page = sb.cdp
    try:
        buttons = page.find_elements('button')
        for btn in buttons:
            try:
                txt = btn.text.strip()
            except Exception:
                txt = ''
            if any(t.lower() in txt.lower() for t in button_texts):
                btn.click()
                return {'mode': 'cdp', 'text': txt}
    except Exception:
        pass

    for text in button_texts:
        try:
            selector = f'button:contains("{text}")'
            if sb.is_element_present(selector):
                sb.click(selector)
                return {'mode': 'selenium', 'text': text}
        except Exception:
            pass

    clicked = sb.execute_script(r"""
        const texts = ['登录', '登入', 'Sign in', 'Login', '提交'];
        const candidates = Array.from(document.querySelectorAll('button, [role="button"], input[type="submit"]'));
        const target = candidates.find((el) => {
            const text = (el.innerText || el.value || el.textContent || '').trim();
            return texts.some((t) => text.toLowerCase().includes(t.toLowerCase()));
        }) || candidates.find((el) => el.type === 'submit') || candidates[0];
        if (!target) return null;
        const text = (target.innerText || target.value || target.textContent || '').trim();
        target.click();
        return text || 'clicked';
    """)
    if clicked:
        return {'mode': 'js', 'text': clicked}
    return None


def verify_signed(sb, allow_refresh=True):
    if allow_refresh:
        sb.open(DASHBOARD_URL)
        sb.sleep(4)
    verify = body_text(sb)
    return has_success(verify), verify


try:
    with SB(uc=True, test=False, locale_code="zh-CN", user_data_dir=PROFILE_DIR, xvfb=False, headed=HEADED) as sb:
        sb.activate_cdp_mode(LOGIN_URL)
        sb.sleep(3)

        if "/login" in sb.get_current_url():
            login_probe = wait_for_login_inputs(sb, timeout=20)
            if not login_probe.get('mode'):
                done(False, '登录页输入框不足', url=sb.get_current_url(), state=login_probe.get('state'))
            try:
                fill_state = fill_login_inputs(sb, login_probe)
            except Exception as e:
                done(False, '登录页输入框填写失败', url=sb.get_current_url(), error=str(e), state=login_probe.get('state'))
            clicked = click_login_button(sb)
            if not clicked:
                done(False, '未找到登录按钮', url=sb.get_current_url(), fill=fill_state, state=login_probe.get('state'))
            sb.sleep(8)

        if OBSERVE_MODE and "/dashboard" in sb.get_current_url():
            sb.sleep(4)
        else:
            sb.open(DASHBOARD_URL)
            sb.sleep(4)
        if "/login" in sb.get_current_url():
            done(False, '访问 dashboard 时回登录页', url=sb.get_current_url())

        body = body_text(sb)
        if has_success(body):
            done(True, '页面已显示已签到', url=sb.get_current_url())

        buttons = sb.cdp.find_elements('button')
        target = None
        for btn in buttons:
            try:
                txt = btn.text.strip()
            except Exception:
                txt = ''
            if txt == '立即签到':
                target = btn
                break
        if not target:
            done(False, '未找到立即签到按钮', url=sb.get_current_url())

        target.scroll_into_view()
        sb.sleep(1.2)
        target.click()
        sb.sleep(1.5)

        step = {'has_modal': False, 'retries': []}
        probe = wait_modal_and_widget(sb, timeout=12)
        step['has_modal'] = bool(probe.get('modal_seen'))
        step['initial_probe'] = probe

        if step['has_modal']:
            for i in range(1, 4):
                retry = {'attempt': i}
                retry['probe_before'] = wait_modal_and_widget(sb, timeout=8)
                retry['failure_seen_before_click'] = bool(retry['probe_before'].get('failure_seen'))
                retry['ready_before_click'] = bool(
                    retry['probe_before'].get('modal_seen')
                    and retry['probe_before'].get('widget_ready')
                    and retry['probe_before'].get('geometry_ready')
                    and retry['probe_before'].get('interactive_ready')
                    and not retry['probe_before'].get('placeholder_only')
                    and (
                        retry['failure_seen_before_click']
                        or retry['probe_before'].get('stable_hits', 0) >= 4
                    )
                )
                retry['wait_before_click_s'] = 3.0 + i
                sb.sleep(retry['wait_before_click_s'])
                retry['state_before_click'] = get_turnstile_state(sb)
                retry['gui_target_before_click'] = get_gui_target(sb)

                state_before = retry['state_before_click']
                gui_before = retry['gui_target_before_click']
                retry['stable_before_click'] = bool(
                    retry['ready_before_click']
                    and rect_is_usable((gui_before or {}).get('rect') or state_before.get('widget_rect'))
                )

                if retry['stable_before_click']:
                    try:
                        retry['visible_click'] = visible_click_turnstile(sb)
                        retry['captcha_action_invoked'] = bool(retry['visible_click'].get('clicked'))
                    except Exception as e:
                        retry['captcha_action_invoked'] = False
                        retry['error'] = str(e)
                else:
                    retry['captcha_action_invoked'] = False
                    retry['skipped_reason'] = 'small_box_not_ready'

                sb.sleep(6 + i)
                retry['state_after_click'] = get_turnstile_state(sb)
                retry['gui_target_after_click'] = get_gui_target(sb)
                body_after = body_text(sb)
                retry['body_hit'] = [x for x in MODAL_TEXTS + SUCCESS_TEXTS + FAILURE_TEXTS if x in body_after]
                retry['token_after_click'] = token_len_from_state(retry['state_after_click'])
                retry['captcha_token_verified'] = has_verified_token(retry['token_after_click'])
                retry['captcha_verified'] = retry['captcha_token_verified']
                step['retries'].append(retry)

                if has_success(body_after):
                    ok, _ = verify_signed(sb, allow_refresh=not OBSERVE_MODE)
                    if ok:
                        done(True, '刷新后确认已签到', url=sb.get_current_url(), step=step)

                if retry['captcha_token_verified']:
                    sb.sleep(3)
                    ok, _ = verify_signed(sb, allow_refresh=not OBSERVE_MODE)
                    if ok:
                        done(True, 'Turnstile token 生效，刷新后确认已签到', url=sb.get_current_url(), step=step)

            if OBSERVE_MODE:
                hold_browser_for_observation(sb, 'HOHAI 验证未通过，保留现场供观察', step)
        else:
            step['body_hit'] = probe.get('body_hit', [])
            step['turnstile_state'] = probe.get('state', {})
            if OBSERVE_MODE:
                hold_browser_for_observation(sb, 'HOHAI 未检测到稳定验证控件，保留现场供观察', step)

        ok, verify = verify_signed(sb, allow_refresh=not OBSERVE_MODE)
        if ok:
            done(True, '刷新后确认已签到', url=sb.get_current_url(), step=step)

        if OBSERVE_MODE:
            hold_browser_for_observation(sb, 'HOHAI 最终未确认签到成功，保留现场供观察', step)

        done(False, '未找到签到成功证据', url=sb.get_current_url(), step=step)
except SystemExit:
    raise
except Exception as e:
    done(False, "浏览器启动或脚本顶层异常", error="%s: %s" % (type(e).__name__, str(e)), traceback=traceback.format_exc())
