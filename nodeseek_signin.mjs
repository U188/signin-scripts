#!/usr/bin/env node
import process from 'process';
import { execFileSync } from 'child_process';

const OPENCLAW_BIN = process.env.OPENCLAW_BIN || '/usr/local/node/bin/openclaw';
const CDP_HTTP = process.env.OPENCLAW_CDP_HTTP || 'http://127.0.0.1:18800';

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function jsonFetch(url, options = {}) {
  const res = await fetch(url, options);
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`${url} -> HTTP ${res.status}${text ? ` ${text.slice(0, 200)}` : ''}`);
  }
  return res.json();
}

async function ensureBrowser() {
  try {
    execFileSync(OPENCLAW_BIN, ['--log-level', 'silent', 'browser', 'start'], {
      stdio: ['ignore', 'ignore', 'ignore'],
      timeout: 20000,
    });
  } catch {}

  let lastErr;
  for (let i = 0; i < 10; i += 1) {
    try {
      await jsonFetch(`${CDP_HTTP}/json/version`);
      return;
    } catch (err) {
      lastErr = err;
      await sleep(1000);
    }
  }
  throw lastErr;
}

async function openPage(url) {
  const opened = await jsonFetch(`${CDP_HTTP}/json/new?${encodeURIComponent(url)}`, { method: 'PUT' });
  return opened?.id || null;
}

async function findTarget(targetId) {
  const targets = await jsonFetch(`${CDP_HTTP}/json/list`);
  return targets.find((t) => t.id === targetId) || null;
}

async function withWs(targetId, fn) {
  const target = await findTarget(targetId);
  if (!target?.webSocketDebuggerUrl) throw new Error('找不到页面 websocket');
  return await new Promise((resolve, reject) => {
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    let seq = 0;
    const pending = new Map();
    let closed = false;

    const cleanup = () => {
      if (closed) return;
      closed = true;
      for (const { reject } of pending.values()) {
        reject(new Error('CDP websocket 已关闭'));
      }
      pending.clear();
      try { ws.close(); } catch {}
    };

    const call = (method, params = {}) => new Promise((resolveCall, rejectCall) => {
      const id = ++seq;
      pending.set(id, { resolve: resolveCall, reject: rejectCall });
      ws.send(JSON.stringify({ id, method, params }), (err) => {
        if (err) {
          pending.delete(id);
          rejectCall(err);
        }
      });
    });

    ws.addEventListener('open', async () => {
      try {
        const result = await fn({ call });
        cleanup();
        resolve(result);
      } catch (err) {
        cleanup();
        reject(err);
      }
    });

    ws.addEventListener('message', (event) => {
      try {
        const raw = typeof event.data === 'string' ? event.data : '';
        const msg = JSON.parse(raw);
        if (!msg.id) return;
        const item = pending.get(msg.id);
        if (!item) return;
        pending.delete(msg.id);
        if (msg.error) item.reject(new Error(msg.error.message || JSON.stringify(msg.error)));
        else item.resolve(msg.result || {});
      } catch (err) {}
    });

    ws.addEventListener('error', (err) => {
      cleanup();
      reject(err);
    });

    ws.addEventListener('close', () => {
      if (!closed) {
        cleanup();
      }
    });
  });
}

async function evalPage(targetId, fn) {
  return await withWs(targetId, async ({ call }) => {
    await call('Runtime.enable');
    const res = await call('Runtime.evaluate', {
      expression: `(${fn})()`,
      returnByValue: true,
      awaitPromise: true,
    });
    return res?.result?.value;
  });
}

async function navigate(targetId, url) {
  await withWs(targetId, async ({ call }) => {
    await call('Page.enable');
    await call('Page.navigate', { url });
  });
}

async function closePage(targetId) {
  if (!targetId) return;
  try {
    await fetch(`${CDP_HTTP}/json/close/${targetId}`);
  } catch {}
}

async function clickByText(targetId, terms) {
  const fn = `() => {
    const wanted = ${JSON.stringify(terms)};
    const isVisible = (el) => {
      const style = window.getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return style && style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
    };
    const textOf = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
    const nodes = [...document.querySelectorAll('button, a, [role="button"], input[type="button"], input[type="submit"], div, span')];
    const candidates = nodes.filter((el) => {
      const text = textOf(el);
      return text && isVisible(el) && wanted.some((term) => text.includes(term));
    });
    candidates.sort((a, b) => textOf(a).length - textOf(b).length);
    const el = candidates[0];
    if (!el) return { clicked: false, found: [] };
    const text = textOf(el);
    el.click();
    return { clicked: true, text, found: candidates.slice(0, 5).map(textOf) };
  }`;
  return await evalPage(targetId, fn);
}

async function getBodyText(targetId) {
  const txt = await evalPage(targetId, '() => document.body.innerText');
  return typeof txt === 'string' ? txt : '';
}

function extractReward(text) {
  const m = text.match(/今日签到获得鸡腿\s*(\d+)\s*个?[，,。\s]*当前排名第\s*(\d+)/);
  if (m) return { reward: m[1], rank: m[2] };
  const m2 = text.match(/今日签到获得鸡腿\s*(\d+)/);
  const m3 = text.match(/当前排名第\s*(\d+)/);
  return {
    reward: m2?.[1] || null,
    rank: m3?.[1] || null,
  };
}

function extractChickenTotal(text) {
  const patterns = [
    /等级\s*Lv\s*\d+[\s\S]{0,80}?鸡腿\s*(\d+)\b/,
    /鸡腿\s*(\d+)\s*\n\s*星辰\s*\d+/,
    /鸡腿[:：]?\s*(\d+)\s*\n\s*(?:星辰|通知|主题帖|评论数|粉丝|收藏)\b/,
  ];
  for (const p of patterns) {
    const m = text.match(p);
    if (m) return m[1];
  }
  return null;
}

function hasLoginBlocker(text) {
  return (text.includes('用户名') || text.includes('邮箱')) && text.includes('密码') && text.includes('登录');
}

function hasCaptchaBlocker(text) {
  const signals = ['Cloudflare', 'Turnstile', '验证', '验证码', 'I am human', '人机验证'];
  return signals.some((s) => text.includes(s));
}

async function main() {
  await ensureBrowser();
  let targetId = null;

  try {
    targetId = await openPage('https://www.nodeseek.com/board');
    if (!targetId) throw new Error('无法打开 NodeSeek 签到页');

    await sleep(2200);
    let boardText = await getBodyText(targetId);

    if (hasLoginBlocker(boardText)) {
      console.log('NodeSeek 自动签到战报\n\n[执行受阻] NodeSeek\n🔐 登录状态: 当前未登录，需要先登录 NodeSeek 账号');
      return;
    }
    if (hasCaptchaBlocker(boardText)) {
      console.log('NodeSeek 自动签到战报\n\n[执行受阻] NodeSeek\n🧩 验证阻塞: 出现人机验证，需要你手动完成后我再继续');
      return;
    }

    let claimedThisRun = false;
    let tryLuckResult = null;

    if (boardText.includes('今日还未签到')) {
      const signClick = await clickByText(targetId, ['鸡腿 x', '鸡腿x', '鸡腿 ×', '签到']);
      if (!signClick?.clicked) {
        console.log('NodeSeek 自动签到战报\n\n[执行受阻] NodeSeek\n⚠️ 签到按钮: 检测到今日未签到，但没有找到可点击的签到按钮');
        return;
      }
      claimedThisRun = true;
      await sleep(2500);
      boardText = await getBodyText(targetId);
    }

    const beforeTryLuckText = boardText;
    const tryLuckClick = await clickByText(targetId, ['试试手气']);
    if (tryLuckClick?.clicked) {
      await sleep(1800);
      const afterTryLuckText = await getBodyText(targetId);
      const delta = afterTryLuckText.replace(beforeTryLuckText, '').trim();
      if (delta && !delta.includes('试试手气')) {
        tryLuckResult = delta.split('\n').map((s) => s.trim()).filter(Boolean).slice(0, 3).join(' / ');
      } else {
        const luckMatch = afterTryLuckText.match(/试试手气[^\n]*\n([^\n]+)/);
        if (luckMatch?.[1]) tryLuckResult = luckMatch[1].trim();
      }
      boardText = afterTryLuckText;
    }

    const rewardInfo = extractReward(boardText);
    let totalChicken = extractChickenTotal(boardText);

    if (!totalChicken) {
      await navigate(targetId, 'https://www.nodeseek.com/');
      await sleep(2200);
      const homeText = await getBodyText(targetId);
      totalChicken = extractChickenTotal(homeText);
    }

    const lines = ['NodeSeek 自动签到战报', ''];

    if (claimedThisRun && rewardInfo.reward) {
      lines.push('[签到成功] NodeSeek');
      lines.push(`🎁 今日领取: ${rewardInfo.reward} 个鸡腿🍗`);
    } else if (claimedThisRun) {
      lines.push('[签到成功] NodeSeek');
      lines.push('🎁 今日领取: 已点击签到，但奖励数暂未成功提取');
    } else {
      lines.push('[重复签到] NodeSeek');
      lines.push('ℹ️ 今日状态: 已经领取过鸡腿🍗');
    }

    lines.push(totalChicken ? `🍗 当前鸡腿: ${totalChicken}` : '🍗 当前鸡腿: 未获取到');
    lines.push(rewardInfo.rank ? `🏆 当前排名: 第 ${rewardInfo.rank} 名` : '🏆 当前排名: 未获取到');
    if (tryLuckClick?.clicked) {
      lines.push(`🎲 试试手气: ${tryLuckResult || '已点击，但未抓到明确结果文本'}`);
    }

    console.log(lines.join('\n'));
  } finally {
    await closePage(targetId);
  }
}

main().catch((err) => {
  const detail = [err?.message || String(err), err?.cause?.message, err?.stack].filter(Boolean).join('\n');
  console.log(`NodeSeek 自动签到战报\n\n[执行失败] NodeSeek\n❌ 错误信息: ${detail}`);
  process.exit(1);
});
