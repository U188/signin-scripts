#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
import sys
from datetime import datetime

site = sys.argv[1]
raw = sys.stdin.read().strip()
now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def pick(pattern, text, default=None):
    m = re.search(pattern, text, re.S)
    return m.group(1).strip() if m else default


def find_last_json_object(text):
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        if not (line.startswith('{') and line.endswith('}')):
            continue
        try:
            return json.loads(line)
        except Exception:
            continue
    return None


def summarize_raw_error(text, max_lines=3, max_chars=240):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None

    interesting = []
    for line in lines:
        if any(key in line for key in [
            'Traceback (most recent call last):',
            'Exception:',
            'Error:',
            'selenium.common.exceptions.',
            'RuntimeError:',
            'TimeoutError:',
            'SessionNotCreatedException',
            'cannot connect to chrome',
            'from chrome not reachable',
            'Missing X server or $DISPLAY',
            'The platform failed to initialize',
        ]):
            interesting.append(line)

    if not interesting:
        for line in reversed(lines):
            if '<unknown>' in line or re.match(r'^#\d+\s', line):
                continue
            interesting.append(line)
            break

    summary = ' | '.join(dict.fromkeys(interesting[:max_lines]))
    return summary[:max_chars] if summary else lines[-1][:max_chars]


status = '⚠️ 结果待确认'
details = []

if site == 'hohai':
    data = find_last_json_object(raw)
    if isinstance(data, dict):
        if data.get('ok'):
            msg = data.get('message', '签到成功')
            if '已签到' in msg:
                status = '✅ 签到完成'
            else:
                status = '✅ 执行成功'
            details.append('✅ 浏览器签到流程已跑通')
            details.append(f"✅ 最终结果：{msg}")
            details.append(f"✅ 页面位置：{data.get('url', '未获取到')}")
        else:
            status = '❌ 执行失败'
            details.append(f"❌ 失败原因：{data.get('message', '未知错误')}")
            if data.get('url'):
                details.append(f"⚠️ 页面位置：{data['url']}")
    else:
        status = '❌ 执行失败'
        details.append('❌ 脚本输出不是合法 JSON')
        summary = summarize_raw_error(raw)
        if summary:
            details.append(f"⚠️ 原始输出：{summary}")

elif site == 'nodeseek':
    if '[签到成功]' in raw:
        status = '✅ 签到完成'
    elif '[重复签到]' in raw:
        status = 'ℹ️ 今日已签到'
    elif '[执行受阻]' in raw:
        status = '⚠️ 执行受阻'
    elif '[执行失败]' in raw:
        status = '❌ 执行失败'

    reward = pick(r'今日领取[:：]\s*(.+)', raw)
    chicken = pick(r'当前鸡腿[:：]\s*(.+)', raw)
    rank = pick(r'当前排名[:：]\s*(.+)', raw)
    luck = pick(r'试试手气[:：]\s*(.+)', raw)
    blocker = pick(r'执行受阻\] NodeSeek\n(.+)', raw)
    err = pick(r'执行失败\] NodeSeek\n(.+)', raw)

    if reward: details.append(f'✅ {reward}')
    if chicken: details.append(f'✅ 当前鸡腿：{chicken}')
    if rank: details.append(f'✅ 当前排名：{rank}')
    if luck: details.append(f'✅ 试试手气：{luck}')
    if blocker: details.append(f'⚠️ {blocker}')
    if err: details.append(f'❌ {err}')

elif site == 'nodeloc':
    if '状态：✅ 签到成功' in raw or '✅ 签到成功' in raw:
        status = '✅ 签到完成'
        details.append('✅ 签到按钮已完成提交')
    elif '状态：ℹ️  今日已签到' in raw or '今日已签到' in raw:
        status = 'ℹ️ 今日已签到'
        details.append('✅ 今天已经签过到了')
    else:
        status = '❌ 执行失败'
        err = pick(r'❌ 出错: ([^\n]+)', raw)
        page = pick(r'当前页面：(https?://\S+)', raw)
        if err: details.append(f'❌ 失败原因：{err}')
        if page: details.append(f'⚠️ 当前页面：{page}')
        if not details and raw:
            details.append(f'⚠️ 原始输出：{raw.splitlines()[-1][:120]}')

if not details:
    details.append('⚠️ 没提取到更多细节')

name_map = {
    'hohai': 'HOHAI 自动签到',
    'nodeseek': 'NodeSeek 自动签到',
    'nodeloc': 'NodeLoc 自动签到',
}

title = name_map.get(site, site)
body = [title, '', f'执行结果：{status}', '', '结果明细：']
body.extend([f'• {x}' for x in details])
body.extend(['', f'执行时间：{now}'])
print('\n'.join(body))
