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

    sb.open(DASHBOARD_URL)
    sb.sleep(4)
    if "/login" in sb.get_current_url():
        done(False, '访问 dashboard 时回登录页', url=sb.get_current_url())

    body = body_text(sb)
    if '已签到' in body or '今日已签到' in body:
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
    sb.sleep(2.5)

    step = {'has_modal': False, 'retries': []}
    body = body_text(sb)
    step['has_modal'] = '签到验证' in body
    if step['has_modal']:
        for i in range(1, 4):
            body_before = body_text(sb)
            ready = ('签到验证' in body_before) and ('请完成人机验证以继续签到' in body_before)
            retry = {'attempt': i, 'ready': ready}
            if not ready:
                sb.sleep(2)
                body_before = body_text(sb)
                retry['ready_after_wait'] = ('签到验证' in body_before) and ('请完成人机验证以继续签到' in body_before)
            else:
                retry['ready_after_wait'] = True

            sb.sleep(3 + i)
            try:
                sb.uc_gui_click_captcha()
                retry['clicked'] = True
            except Exception as e:
                retry['clicked'] = False
                retry['error'] = str(e)
            sb.sleep(6 + i)

            body_after = body_text(sb)
            retry['body_hit'] = [x for x in ['签到验证', '请完成人机验证以继续签到', 'Turnstile验证失败', '已签到', '今日已签到', '签到成功'] if x in body_after]
            step['retries'].append(retry)

            if any(x in body_after for x in ['已签到', '今日已签到', '签到成功']):
                sb.open(DASHBOARD_URL)
                sb.sleep(4)
                verify = body_text(sb)
                if any(x in verify for x in ['已签到', '今日已签到', '签到成功']):
                    done(True, '刷新后确认已签到', url=sb.get_current_url(), step=step)

            if 'Turnstile验证失败' not in body_after and '签到验证' not in body_after:
                sb.sleep(3)

    sb.open(DASHBOARD_URL)
    sb.sleep(4)
    verify = body_text(sb)
    if any(x in verify for x in ['已签到', '今日已签到', '签到成功']):
        done(True, '刷新后确认已签到', url=sb.get_current_url(), step=step)

    done(False, '未找到签到成功证据', url=sb.get_current_url(), step=step)
