#!/usr/bin/env python3
"""
NodeLoc 每日自动签到脚本
账号：u1888
复用本机已运行的 Chrome（CDP 18800）
"""
import os
import sys
import asyncio
import subprocess
import time
from datetime import datetime

USERNAME = os.environ.get("NODELOC_USERNAME", "u1888")
PASSWORD = os.environ.get("NODELOC_PASSWORD", "WOWlove123!")
CDP_URL = os.environ.get("CDP_URL", "http://127.0.0.1:18800")
CHROME_BIN = os.environ.get("CHROME_BIN", "/usr/bin/google-chrome")
CHROME_USER_DATA_DIR = os.environ.get("CHROME_USER_DATA_DIR", "/root/.openclaw/browser/openclaw/user-data")
FALLBACK_USER_DATA_DIR = os.environ.get("NODELOC_FALLBACK_USER_DATA_DIR", "/root/.config/nodeloc-cdp")

def ensure_cdp_chrome():
    """确保 18800 上有可连接的 Chrome CDP；没有就临时启动一个。"""
    from urllib.parse import urlparse
    import socket

    parsed = urlparse(CDP_URL)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 18800

    sock = socket.socket()
    sock.settimeout(2)
    try:
        if sock.connect_ex((host, port)) == 0:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] CDP 已在线：{host}:{port}")
            return None
    finally:
        sock.close()

    user_data_dir = CHROME_USER_DATA_DIR
    cmd = [
        CHROME_BIN,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-gpu",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "about:blank",
    ]
    print(f"[{datetime.now().strftime('%H:%M:%S')}] CDP 未在线，启动临时 Chrome: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    for _ in range(20):
        sock = socket.socket()
        sock.settimeout(2)
        try:
            if sock.connect_ex((host, port)) == 0:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 临时 Chrome 已启动：{host}:{port}")
                return proc
        finally:
            sock.close()
        time.sleep(0.5)

    try:
        proc.kill()
    except Exception:
        pass
    raise RuntimeError(f"无法启动 Chrome CDP：{host}:{port}")

async def signin():
    from patchright.async_api import async_playwright

    launched_proc = ensure_cdp_chrome()
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 连接浏览器 {CDP_URL}...")
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(CDP_URL)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await context.new_page()

        try:
            # 登录
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 访问登录页...")
            await page.goto("https://www.nodeloc.com/login", wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(2)

            # 检查是否已登录
            login_name = page.locator('#login-account-name')
            login_pwd = page.locator('#login-account-password')
            if await login_name.count() == 0 and await login_pwd.count() == 0 and "/login" not in page.url:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 已登录状态，直接检查签到...")
            else:
                if "/login" not in page.url:
                    await page.goto("https://www.nodeloc.com/login", wait_until="domcontentloaded", timeout=20000)
                    await asyncio.sleep(2)

                await page.fill('#login-account-name', USERNAME)
                await page.fill('#login-account-password', PASSWORD)
                await page.click('.btn-primary:has-text("登录")')

                login_ok = False
                for _ in range(30):
                    body_text = await page.locator('body').inner_text()
                    if '注册' not in body_text and '登录' not in body_text and '退出' in body_text:
                        login_ok = True
                        break
                    if await page.locator('a:has-text("退出"), button:has-text("退出")').count() > 0:
                        login_ok = True
                        break
                    await asyncio.sleep(1)

                if not login_ok:
                    raise RuntimeError("登录后页面未进入已登录态")

                print(f"[{datetime.now().strftime('%H:%M:%S')}] 登录成功")

            # 跳到首页检查签到按钮
            if "nodeloc.com" not in page.url or "/login" in page.url:
                await page.goto("https://www.nodeloc.com/", wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(3)

            # 检查是否已签到
            checked_in = await page.locator('.checkin-button.checked-in').count()
            if checked_in > 0:
                title = await page.locator('.checkin-button.checked-in').get_attribute('title')
                print(f"ℹ️  {title or '今日已签到'}")
                await page.close()
                return "already"

            # 点击签到
            checkin_btn = page.locator('button.checkin-button')
            await asyncio.sleep(1)
            if await checkin_btn.count() == 0:
                print("❌ 未找到签到按钮")
                print(f"当前页面：{page.url}")
                await page.close()
                sys.exit(1)

            await checkin_btn.click()
            await asyncio.sleep(2)

            # 确认结果
            checked_in = await page.locator('.checkin-button.checked-in').count()
            if checked_in > 0:
                title = await page.locator('.checkin-button.checked-in').get_attribute('title')
                print(f"✅ 签到成功！{title or ''}")
                await page.close()
                return "success"
            else:
                print("⚠️  签到状态未知，请手动确认")
                await page.close()
                return "unknown"
        except Exception as e:
            print(f"❌ 出错: {e}")
            print(f"当前页面：{page.url}")
            await page.close()
            sys.exit(1)
        finally:
            if launched_proc:
                try:
                    launched_proc.terminate()
                except Exception:
                    pass

if __name__ == "__main__":
    result = asyncio.run(signin())
    print(f"\nNodeLoc 签到战报")
    print(f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if result == "success":
        print("状态：✅ 签到成功")
    elif result == "already":
        print("状态：ℹ️  今日已签到")
    else:
        print("状态：⚠️  未知")
