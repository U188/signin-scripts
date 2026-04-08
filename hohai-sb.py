#!/root/.openclaw/venvs/seleniumbase/bin/python
# -*- coding: utf-8 -*-

import json
import os
import sys
from seleniumbase import SB

BASE = "https://tv.hohai.eu.org"
LOGIN_URL = f"{BASE}/login"
DASHBOARD_URL = f"{BASE}/dashboard"
USERNAME = os.environ.get("HOHAI_USERNAME", "u1888")
PASSWORD = os.environ.get("HOHAI_PASSWORD", "zn552297")
PROFILE_DIR = os.environ.get("HOHAI_SB_PROFILE", "/root/.config/seleniumbase-hohai")
HEADED = os.environ.get("HOHAI_HEADED", "1") == "1"
KEEP_OPEN_ON_FAIL = os.environ.get("HOHAI_KEEP_OPEN_ON_FAIL", "0") == "1"
OBSERVE_MODE = os.environ.get("HOHAI_OBSERVE_MODE", "0") == "1"
HOLD_OPEN_SECS = int(os.environ.get("HOHAI_HOLD_OPEN_SECS", "600"))

SUCCESS_TEXTS = ['已签到', '今日已签到', '签到成功']
MODAL_TEXTS = ['签到验证', '请完成人机验证以继续签到']


def done(ok, message, **extra):
    data = {"ok": ok, "message": message}
    data.update(extra)
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


def get_turnstile_state(sb):
    js = r"""
    const token = document.querySelector('input[name="cf-turnstile-response"]')?.value || '';
    const widget = document.querySelector('.turnstile-widget, [class*="turnstile"], [id*="turnstile"]');
    const iframe = document.querySelector('iframe[src*="turnstile"], iframe[src*="challenge-platform"]');
    const rect = widget ? widget.getBoundingClientRect() : null;
    return {
      token_len: token.length,
      has_widget: !!widget,
      has_iframe: !!iframe,
      widget_rect: rect ? {x: rect.x, y: rect.y, width: rect.width, height: rect.height} : null,
      widget_html: widget ? widget.outerHTML.slice(0, 400) : ''
    };
    """
    try:
        return sb.execute_script(js) or {}
    except Exception:
        return {}


def wait_modal_and_widget(sb, timeout=12):
    rounds = int(timeout / 0.5)
    last = {'modal_seen': False, 'widget_ready': False, 'state': {}, 'body_hit': []}
    for _ in range(rounds):
        text = body_text(sb)
        state = get_turnstile_state(sb)
        modal_seen = modal_ready(text)
        widget_ready = bool(state.get('has_widget') or state.get('has_iframe'))
        last = {
            'modal_seen': modal_seen,
            'widget_ready': widget_ready,
            'state': state,
            'body_hit': [x for x in MODAL_TEXTS + ['Turnstile验证失败'] if x in text],
        }
        if modal_seen:
            return last
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


def verify_signed(sb, allow_refresh=True):
    if allow_refresh:
        sb.open(DASHBOARD_URL)
        sb.sleep(4)
    verify = body_text(sb)
    return has_success(verify), verify


with SB(uc=True, test=False, locale_code="zh-CN", user_data_dir=PROFILE_DIR, xvfb=False, headed=HEADED) as sb:
    sb.activate_cdp_mode(LOGIN_URL)
    sb.sleep(3)

    if "/login" in sb.get_current_url():
        page = sb.cdp
        inputs = page.find_elements('input')
        if len(inputs) < 2:
            done(False, '登录页输入框不足', url=sb.get_current_url(), count=len(inputs))
        inputs[0].click()
        sb.sleep(0.5)
        inputs[0].press_keys(USERNAME)
        sb.sleep(0.5)
        inputs[1].click()
        sb.sleep(0.5)
        inputs[1].press_keys(PASSWORD)
        sb.sleep(0.8)
        buttons = page.find_elements('button')
        clicked = False
        for btn in buttons:
            try:
                txt = btn.text.strip()
            except Exception:
                txt = ''
            if txt == '登录':
                btn.click()
                clicked = True
                break
        if not clicked:
            done(False, '未找到登录按钮', url=sb.get_current_url())
        sb.sleep(6)

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
            retry['wait_before_click_s'] = 3 + i
            sb.sleep(3 + i)
            retry['state_before_click'] = get_turnstile_state(sb)

            try:
                sb.uc_gui_handle_captcha()
                retry['clicked'] = True
            except Exception as e:
                retry['clicked'] = False
                retry['error'] = str(e)

            sb.sleep(6 + i)
            retry['state_after_click'] = get_turnstile_state(sb)
            body_after = body_text(sb)
            retry['body_hit'] = [x for x in MODAL_TEXTS + SUCCESS_TEXTS + ['Turnstile验证失败'] if x in body_after]
            step['retries'].append(retry)

            if has_success(body_after):
                ok, _ = verify_signed(sb, allow_refresh=not OBSERVE_MODE)
                if ok:
                    done(True, '刷新后确认已签到', url=sb.get_current_url(), step=step)

            token_len = retry['state_after_click'].get('token_len', 0)
            if token_len > 0:
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
