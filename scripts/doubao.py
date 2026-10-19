#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""豆包（doubao.com）网页版 AI 搜索爬虫

流程：
  1. 连接 Playwright MCP（接管已登录 Chrome，token 用环境变量 PLAYWRIGHT_MCP_EXTENSION_TOKEN）
  2. 打开豆包聊天页 https://www.doubao.com/chat/（即新会话）
  3. 把关键词输入 ProseMirror 编辑器并回车发送
  4. 轮询等待回答流式结束（data-streaming=false）
  5. 抽取全部消息（user/assistant）与外部引用链接
  6. 保存到技能根目录 data/<日期>/doubao_<关键词>_<时间>.{json,md}

用法：
  python scripts/doubao.py "关键词"
  python scripts/doubao.py --keyword "关键词" --wait-rounds 60 --round-secs 3 --screenshot
"""

import argparse
import asyncio
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import PlaywrightMCP, safe_name, save_result, platform_dir

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.path.join(SKILL_ROOT, "data")
DOUBAO_URL = "https://www.doubao.com/chat/"

EDITOR_SEL = 'div[contenteditable="true"].tiptap'


def parse_json_result(res):
    """把 MCP 工具结果解析成 Python 对象（parse_result 可能返回 JSON 字符串，需要二次解析）。"""
    text = PlaywrightMCP.parse_result(res)
    if text is None:
        return None
    if isinstance(text, (dict, list)):
        return text
    try:
        obj = json.loads(text)
        return obj
    except Exception:
        return text


async def send_question(client, keyword):
    """打开新会话并发送问题。验证码是发送后才弹的（跨域 iframe，主文档无文字）：
    通过可见的 #captcha_container / 可见的 verifycenter|captcha iframe 两路检测，
    命中立即返回 CAPTCHA 不重试。不做整页文字匹配（页面常驻"按住空格说话""滑动探索"等文案会误判）。"""
    keyword = keyword + '，请在结尾把 llm 传给搜索工具的 query、搜索召回的全部检索条目（网站名称 + 完整链接）完整列出来'
    code = """
    async (page) => {
        const detectCaptcha = async () => {
            return await page.evaluate(() => {
                const cc = document.querySelector('#captcha_container');
                if (cc && cc.offsetWidth > 0 && cc.offsetHeight > 0) return 'container';
                const cap = Array.from(document.querySelectorAll('iframe')).find(f => (f.src || '').indexOf('verifycenter/captcha') >= 0 || (f.src || '').indexOf('rmc.bytedance.com') >= 0);
                if (cap && cap.offsetWidth > 0 && cap.offsetHeight > 0) return 'iframe';
                return null;
            });
        };
        const sent = async () => {
            return await page.evaluate(() => {
                const cc = document.querySelector('#captcha_container');
                if (cc && cc.offsetWidth > 0 && cc.offsetHeight > 0) return 'CAPTCHA:container';
                const cap = Array.from(document.querySelectorAll('iframe')).find(f => (f.src || '').indexOf('verifycenter/captcha') >= 0 || (f.src || '').indexOf('rmc.bytedance.com') >= 0);
                if (cap && cap.offsetWidth > 0 && cap.offsetHeight > 0) return 'CAPTCHA:iframe';
                const hasMsg = !!document.querySelector('[data-message-id]');
                const p = location.pathname;
                // 豆包新会话 URL 就是 /chat/local_<id>，local_ 前缀是正常会话而非验证码
                const onConv = p.indexOf('/chat/') === 0 && p.length > '/chat/'.length;
                return (hasMsg || onConv) ? 'OK' : 'NO';
            });
        };
        const gotoFresh = async () => {
            await page.goto('%s', {waitUntil: 'domcontentloaded', timeout: 60000});
            await page.waitForTimeout(4000);
        };
        const kw = %s;
        await gotoFresh();
        const cap0 = await detectCaptcha();
        if (cap0) return 'CAPTCHA: 检测到人机验证（' + cap0 + '），请手动完成验证后重试';
        let lastErr = '';
        for (let attempt = 0; attempt < 4; attempt++) {
            try {
                const ed = page.locator('%s').nth(0);
                await ed.waitFor({state: 'attached', timeout: 20000});
                await ed.click({force: true, timeout: 10000});
                await ed.pressSequentially(kw, {delay: 20});
                const typed = (await ed.innerText()).trim();
                if (typed !== kw) {
                    lastErr = 'typed mismatch: ' + typed.slice(0, 30);
                    await page.keyboard.press('Control+A');
                    await page.keyboard.press('Delete');
                    await page.waitForTimeout(500);
                    await gotoFresh();
                    continue;
                }
                await page.keyboard.press('Enter');
                for (let i = 0; i < 15; i++) {
                    await page.waitForTimeout(1000);
                    const c = await detectCaptcha();
                    if (c) return 'CAPTCHA: 检测到人机验证（' + c + '），请手动完成验证后重试';
                    const s = await sent();
                    if (s === 'OK') return 'SENT';
                    if (s.indexOf('CAPTCHA') === 0) return 'CAPTCHA: 检测到' + s.slice(8) + '（人机验证），请手动完成验证后重试';
                }
                lastErr = 'no message after send';
                await page.keyboard.press('Control+A');
                await page.keyboard.press('Delete');
                await page.waitForTimeout(500);
            } catch (e) {
                lastErr = String(e).slice(0, 120);
                await page.waitForTimeout(1000);
            }
            await gotoFresh();
        }
        const finalUrl = await page.evaluate(() => location.href);
        if (finalUrl.indexOf('/chat/') >= 0 && finalUrl.indexOf('/chat/') < finalUrl.length - 6) {
            return 'SENT: ' + finalUrl;
        }
        const capEnd = await detectCaptcha();
        if (capEnd) return 'CAPTCHA: 检测到人机验证（' + capEnd + '），请手动完成验证后重试';
        return 'SEND_FAIL: ' + lastErr;
    }
    """ % (DOUBAO_URL, json.dumps(keyword, ensure_ascii=False), EDITOR_SEL)
    res = await client.call_tool("browser_run_code_unsafe", {"code": code})
    return client.parse_result(res)

async def wait_answer(client, wait_rounds, round_secs):
    """轮询等待回答结束。完成=助手消息 data-streaming=false 且文本连续两轮不变。
    验证码检测：可见的 #captcha_container + 可见的 verifycenter|captcha iframe。
    不做整页文字匹配（正常页面常驻"按住空格说话"等文案，且回答正文可能含"验证码/重试"等词）。"""
    timeout_ms = int(wait_rounds * round_secs * 1000)
    code = """
    async (page) => {
        const start = Date.now();
        const timeoutMs = %d;
        let lastLen = -1;
        let stableRounds = 0;
        let zeroRounds = 0;
        let lastState = null;
        while (Date.now() - start < timeoutMs) {
            await page.waitForTimeout(%d);
            const st = await page.evaluate(() => {
                const body = document.body ? document.body.innerText : '';
                const msgs = document.querySelectorAll('[data-message-id]');
                const last = msgs.length ? msgs[msgs.length - 1] : null;
                const stream = last ? last.querySelector('[data-streaming]') : null;
                const ds = stream ? stream.getAttribute('data-streaming') : null;
                const lastText = last ? (last.innerText || '').trim() : '';
                const isUser = last ? (last.className || '').toString().includes('justify-end') : false;
                let errText = '';
                if (/生成失败|请求失败|网络异常|出错了|请重试|重试/.test(body)) {
                    errText = body.slice(0, 80);
                }
                // 真实失败横幅通常是消息文本以错误词开头（如"生成失败，请重试"）；回答正文中段出现"重试"不算
                const lastErrStart = /^(生成失败|请求失败|网络异常|出错了|请重试)/.test(lastText);
                let capLocal = '';
                const cc = document.querySelector('#captcha_container');
                if (cc && cc.offsetWidth > 0 && cc.offsetHeight > 0) capLocal = 'container';
                const cap = Array.from(document.querySelectorAll('iframe')).find(f => (f.src || '').indexOf('verifycenter/captcha') >= 0 || (f.src || '').indexOf('rmc.bytedance.com') >= 0);
                if (cap && cap.offsetWidth > 0 && cap.offsetHeight > 0) capLocal = 'iframe';
                return {
                    msgs: msgs.length,
                    isUser: isUser,
                    ds: ds,
                    lastLen: lastText.length,
                    errText: errText,
                    lastErrStart: lastErrStart,
                    capLocal: capLocal
                };
            });
            lastState = st;
            if (st.capLocal) {
                return {done: false, error: '检测到' + st.capLocal + '（人机验证），请手动完成验证后重试', state: st};
            }
            if ((st.errText && st.lastLen === 0) || st.lastErrStart) {
                return {done: false, error: st.errText, state: st};
            }
            if (st.msgs === 0) {
                zeroRounds += 1;
                if (zeroRounds >= 3) {
                    return {done: false, error: '页面没有消息（发送失败或页面状态异常）', state: st};
                }
            } else {
                zeroRounds = 0;
            }
            // 完成判定：助手消息已结束（ds=false）且文本连续两轮不变
            if (st.msgs > 0 && !st.isUser && st.ds === 'false' && st.lastLen > 0 && st.lastLen === lastLen) {
                stableRounds += 1;
                if (stableRounds >= 2) {
                    return {done: true, state: st};
                }
            } else {
                stableRounds = 0;
            }
            lastLen = st.lastLen;
        }
        return {done: false, state: lastState};
    }
    """ % (timeout_ms, int(round_secs * 1000))
    res = await client.call_tool("browser_run_code_unsafe", {"code": code})
    return parse_json_result(res)

async def extract_messages(client):
    """从页面抽取全部消息与引用链接（DOM 逻辑必须包在 page.evaluate 里）。"""
    code = """
    async (page) => {
        const result = await page.evaluate(() => {
            const ml = document.querySelector('[class*="message-list"]');
            if (!ml) {
                return {err: 'no message-list', url: location.href, title: document.title};
            }
            const msgs = [];
            ml.querySelectorAll('[data-message-id]').forEach((el) => {
                const cls = (el.className || '').toString();
                const isUser = cls.includes('justify-end');
                let contentText = '';
                el.querySelectorAll('[data-streaming]').forEach((ce) => {
                    const t = (ce.innerText || '').trim();
                    if (t.length > contentText.length) contentText = t;
                });
                const txt = contentText || (el.innerText || '').trim();
                const links = [];
                el.querySelectorAll('a[href]').forEach((a) => {
                    const href = (a.href || '').trim();
                    if (href && href.indexOf('doubao.com') < 0) {
                        links.push({title: (a.innerText || '').trim(), href: href});
                    }
                });
                msgs.push({role: isUser ? 'user' : 'assistant', text: txt, links: links});
            });
            const assistants = msgs.filter(m => m.role === 'assistant');
            const last = assistants.length ? assistants[assistants.length - 1] : null;
            const seen = new Set();
            const refs = (last ? last.links : []).filter(r => {
                if (seen.has(r.href)) return false;
                seen.add(r.href);
                return true;
            });
            return {
                err: null,
                url: location.href,
                title: document.title,
                msgs: msgs,
                answer: last ? last.text : '',
                references: refs
            };
        });
        return result;
    }
    """
    res = await client.call_tool("browser_run_code_unsafe", {"code": code})
    return parse_json_result(res)

async def save_screenshot(client, path):
    code = """
    async (page) => {
        await page.screenshot({path: '%s', fullPage: false});
        return 'OK';
    }
    """ % path.replace("\\", "/")
    res = await client.call_tool("browser_run_code_unsafe", {"code": code})
    return client.parse_result(res)


async def run(keyword, wait_rounds=60, round_secs=3, screenshot=False, client=None):
    """执行一次豆包搜索，返回结果 dict（含保存路径）。"""
    owns_client = client is None
    if owns_client:
        token = os.environ.get("PLAYWRIGHT_MCP_EXTENSION_TOKEN")
        client = PlaywrightMCP(token)
    now_iso = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result = {
        "platform": "doubao",
        "keyword": keyword,
        "queried_at": now_iso,
        "url": "",
        "title": "",
        "messages": [],
        "answer": "",
        "references": [],
        "status": "ok",
        "error": "",
        "screenshot": "",
    }
    try:
        if not await client.connect():
            result["status"] = "error"
            result["error"] = "Playwright MCP 连接失败（请确认 Chrome 已启动且安装了 Playwright MCP 扩展）"
            return result
        url = await send_question(client, keyword)
        send_failed = bool(url and (url.startswith("SEND_FAIL") or url.startswith("CAPTCHA") or url.startswith("### Error") or url.startswith("GOTO_ERR")))
        captcha_blocked = bool(url and url.startswith("CAPTCHA"))
        result["url"] = url or ""
        if send_failed:
            result["status"] = "error"
            if captcha_blocked:
                result["error"] = str(url)
            else:
                result["error"] = "发送问题失败：%s" % str(url)[:200]
        else:
            wait_res = await wait_answer(client, wait_rounds, round_secs)
            if isinstance(wait_res, dict) and wait_res.get("error"):
                result["status"] = "error"
                result["error"] = wait_res["error"]
            elif isinstance(wait_res, dict) and not wait_res.get("done"):
                result["status"] = "error"
                result["error"] = "等待回答超时（%s 轮 x %s 秒）" % (wait_rounds, round_secs)
        extracted = None
        if not captcha_blocked:
            extracted = await extract_messages(client)
        if isinstance(extracted, dict):
            if extracted.get("err"):
                result["status"] = "error"
                result["error"] = extracted["err"]
            result["url"] = extracted.get("url") or result["url"]
            result["title"] = extracted.get("title") or ""
            result["messages"] = extracted.get("msgs") or []
            result["answer"] = extracted.get("answer") or ""
            result["references"] = extracted.get("references") or []
        if screenshot:
            shot = os.path.join(platform_dir("doubao"),
                                "doubao_%s_%s.png" % (safe_name(keyword), datetime.datetime.now().strftime("%H%M%S")))
            await save_screenshot(client, shot)
            result["screenshot"] = shot
        if not result["answer"] and not captcha_blocked:
            result["status"] = "error"
            result["error"] = (result["error"] or "") + " 未提取到回答文本"
    except Exception as e:
        result["status"] = "error"
        result["error"] = "%s: %s" % (type(e).__name__, str(e))
    finally:
        if owns_client:
            try:
                await client.close_tab()
            except Exception:
                pass
            try:
                await client.disconnect()
            except Exception:
                pass
    json_path, md_path = save_result(result)
    result["json_path"] = json_path
    result["md_path"] = md_path
    return result



def print_result(result):
    print("平台：%s" % result["platform"])
    print("关键词：%s" % result["keyword"])
    print("状态：%s" % result["status"])
    if result["error"]:
        print("错误：%s" % result["error"])
    print("回答长度：%s 字" % len(result["answer"]))
    print("引用条数：%s" % len(result["references"]))
    print("JSON 结果：%s" % result.get("json_path"))
    print("MD 结果：%s" % result.get("md_path"))
    if result.get("screenshot"):
        print("截图：%s" % result["screenshot"])

def main():
    parser = argparse.ArgumentParser(description="豆包网页版 AI 搜索爬虫")
    parser.add_argument("keyword", nargs="?", help="搜索关键词/问题")
    parser.add_argument("--keyword", dest="keyword_opt", help="搜索关键词/问题（与位置参数二选一）")
    parser.add_argument("--wait-rounds", type=int, default=60, help="最长等待轮数（默认 60）")
    parser.add_argument("--round-secs", type=int, default=3, help="每轮等待秒数（默认 3）")
    parser.add_argument("--screenshot", action="store_true", help="同时保存页面截图")
    args = parser.parse_args()
    keyword = args.keyword or args.keyword_opt
    if not keyword:
        parser.error("请提供关键词，例如：python scripts/doubao.py \"什么是豆包\"")
    result = asyncio.run(run(keyword, wait_rounds=args.wait_rounds, round_secs=args.round_secs, screenshot=args.screenshot))
    print_result(result)


if __name__ == "__main__":
    main()












