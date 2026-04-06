#!/usr/bin/env node
import { execFileSync } from 'child_process';
import process from 'process';

const OPENCLAW_BIN = process.env.OPENCLAW_BIN || '/usr/local/node/bin/openclaw';

function sleepMs(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function runOpenClaw(args, { json = false, retries = 3 } = {}) {
  // --json 需插在子命令之后（openclaw --log-level silent browser --json status）
  let finalArgs;
  if (json) {
    const firstNonFlag = args.findIndex(a => !a.startsWith('-'));
    const insertAt = firstNonFlag >= 0 ? firstNonFlag + 1 : args.length;
    finalArgs = ['--log-level', 'silent', ...args.slice(0, insertAt), '--json', ...args.slice(insertAt)];
  } else {
    finalArgs = ['--log-level', 'silent', ...args];
  }

  let lastErr;
  for (let attempt = 1; attempt <= retries; attempt += 1) {
    try {
      const out = execFileSync(OPENCLAW_BIN, finalArgs, {
        encoding: 'utf8',
        stdio: ['ignore', 'pipe', 'ignore'],
        timeout: 90000,
      }).trim();
      return json ? JSON.parse(out) : out;
    } catch (err) {
      lastErr = err;
      const msg = [err?.message, err?.stderr?.toString?.(), err?.stdout?.toString?.()].filter(Boolean).join('\n');
      const retryable = /gateway timeout|timed out|ECONNRESET|ECONNREFUSED|socket hang up/i.test(msg);
      if (!retryable || attempt >= retries) throw err;
      const delay = attempt * 1500;
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, delay);
    }
  }
  throw lastErr;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function wait(ms) {
  runOpenClaw(['browser', 'wait', '--time', String(ms)]);
  await sleep(50);
}

function browserJson(args) {
  return runOpenClaw(['browser', ...args], { json: true });
}

function browserText(args) {
  return runOpenClaw(['browser', ...args], { json: false });
}

function ensureBrowser() {
  const status = browserJson(['status']);
  if (!status?.running || !status?.cdpReady) {
    browserText(['start']);
  }
}

function evalPage(targetId, fn) {
  const res = browserJson(['evaluate', '--target-id', targetId, '--fn', fn]);
  return res?.result;
}

function openPage(url) {
  const opened = browserJson(['open', url]);
  return opened?.targetId;
}

function navigate(targetId, url) {
  browserText(['navigate', '--target-id', targetId, url]);
}

function closePage(targetId) {
  if (!targetId) return;
  try {
    browserText(['close', '--target-id', targetId]);
  } catch {}
}

function stopBrowser() {
  try {
    browserText(['stop']);
  } catch {}
}

function clickByText(targetId, terms) {
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
  return evalPage(targetId, fn);
}

function getBodyText(targetId) {
  const txt = evalPage(targetId, '() => document.body.innerText');
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
  ensureBrowser();
  let targetId = null;

  try {
    targetId = openPage('https://www.nodeseek.com/board');
    if (!targetId) throw new Error('无法打开 NodeSeek 签到页');

    await wait(2200);
    let boardText = getBodyText(targetId);

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
      const signClick = clickByText(targetId, ['鸡腿 x', '鸡腿x', '鸡腿 ×', '签到']);
      if (!signClick?.clicked) {
        console.log('NodeSeek 自动签到战报\n\n[执行受阻] NodeSeek\n⚠️ 签到按钮: 检测到今日未签到，但没有找到可点击的签到按钮');
        return;
      }
      claimedThisRun = true;
      await wait(2500);
      boardText = getBodyText(targetId);
    }

    const beforeTryLuckText = boardText;
    const tryLuckClick = clickByText(targetId, ['试试手气']);
    if (tryLuckClick?.clicked) {
      await wait(1800);
      const afterTryLuckText = getBodyText(targetId);
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
      navigate(targetId, 'https://www.nodeseek.com/');
      await wait(2200);
      const homeText = getBodyText(targetId);
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
    closePage(targetId);
    stopBrowser();
  }
}

main().catch((err) => {
  console.log(`NodeSeek 自动签到战报\n\n[执行失败] NodeSeek\n❌ 错误信息: ${err?.message || String(err)}`);
  process.exit(1);
});
