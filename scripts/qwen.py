#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""千问（qianwen.com）网页版 AI 搜索爬虫

流程：
  1. 连接 Playwright MCP（接管已登录 Chrome）
  2. 打开 https://www.qianwen.com/（通义会重定向过来，即新会话）
  3. 关键词输入 contenteditable 文本框并回车发送
  4. 轮询等待回答完成（最后一段 .qk-markdown 长度连续两轮不变，且无"正在…"状态）
  5. 抽取助手回答（.qk-markdown）与引用链接
  6. 保存到技能根目录 data/<日期>/qwen/qwen_<关键词>_<时间>.{json,md}

用法：
  python scripts/qwen.py "关键词"
  python scripts/search.py --platform qwen --keyword "关键词"
"""

import argparse
import asyncio
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import PlaywrightMCP, safe_name, save_result, platform_dir

QWEN_URL = "https://www.qianwen.com/"
INPUT_SEL = 'div[contenteditable="true"][role="textbox"]'


def parse_json_result(res):
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
    """打开新会话并发送问题。重点：发送按钮必须"人手式"点击（分步移动鼠标再点击），
    用 locator.click 或回车会被千问风控识别，回答一律变成"我无法回答这个问题"。
    发送前清空草稿、逐字输入后校验，再人手式点发送按钮。"""
    keyword = keyword + '，请在结尾把 llm 传给搜索工具的 query、搜索召回的全部检索条目（网站名称 + 完整链接）完整列出来'
    code = """
    async (page) => {
        const clickSendHuman = async () => {
            const pos = await page.evaluate(() => {
                const el = document.querySelector('[aria-label="发送消息"]');
                if (!el) return null;
                const r = el.getBoundingClientRect();
                return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
            });
            if (!pos) return false;
            await page.mouse.move(pos.x - 150, pos.y + 30, {steps: 6});
            await page.waitForTimeout(150 + Math.round(Math.random() * 100));
            await page.mouse.move(pos.x - 60, pos.y - 12, {steps: 5});
            await page.waitForTimeout(120 + Math.round(Math.random() * 100));
            await page.mouse.move(pos.x, pos.y, {steps: 4});
            await page.waitForTimeout(100 + Math.round(Math.random() * 80));
            await page.mouse.click(pos.x, pos.y);
            return true;
        };
        const clearEditor = async (ed) => {
            for (let i = 0; i < 3; i++) {
                try { await ed.click({force: true, timeout: 3000}); } catch (e) {}
                await page.keyboard.press('Control+A');
                await page.keyboard.press('Delete');
                await page.waitForTimeout(350);
                const t = (await ed.innerText()).trim();
                if (!t) return true;
            }
            return (await ed.innerText()).trim() === '';
        };
        await page.goto('%s', {waitUntil: 'domcontentloaded', timeout: 60000});
        await page.waitForTimeout(8000);
        const ed = page.locator('%s').nth(0);
        await ed.waitFor({state: 'attached', timeout: 30000});
        const kw = %s;
        let lastErr = '';
        for (let attempt = 0; attempt < 4; attempt++) {
            try {
                await clearEditor(ed);
                await ed.click({force: true, timeout: 5000});
                await ed.pressSequentially(kw, {delay: 40});
                const typed = (await ed.innerText()).trim();
                if (typed !== kw) {
                    lastErr = 'typed mismatch: ' + typed.slice(0, 30);
                    continue;
                }
                await page.waitForTimeout(400 + Math.round(Math.random() * 300));
                const clicked = await clickSendHuman();
                if (!clicked) {
                    await page.keyboard.press('Enter');
                }
                for (let i = 0; i < 15; i++) {
                    await page.waitForTimeout(1000);
                    const hasMsg = await page.evaluate(() => !!document.querySelector('.chat-round'));
                    if (hasMsg) return 'SENT';
                }
                lastErr = 'no message after send';
            } catch (e) {
                lastErr = String(e).slice(0, 120);
                await page.waitForTimeout(1500);
            }
        }
        return 'SEND_FAIL: ' + lastErr;
    }
    """ % (QWEN_URL, INPUT_SEL, json.dumps(keyword, ensure_ascii=False))
    res = await client.call_tool("browser_run_code_unsafe", {"code": code})
    return client.parse_result(res)

async def wait_answer(client, wait_rounds, round_secs):
    """轮询等待回答结束。完成=最后一段 .qk-markdown 长度连续两轮不变且无"正在…"状态。"""
    timeout_ms = int(wait_rounds * round_secs * 1000)
    code = """
    async (page) => {
        const start = Date.now();
        const timeoutMs = %d;
        let prevLen = -1;
        let stable = 0;
        let zero = 0;
        let lastState = null;
        while (Date.now() - start < timeoutMs) {
            await page.waitForTimeout(%d);
            const st = await page.evaluate(() => {
                const body = document.body ? document.body.innerText : '';
                const mds = document.querySelectorAll('.qk-markdown');
                const last = mds.length ? mds[mds.length - 1] : null;
                const len = last ? (last.innerText || '').trim().length : 0;
                const generating = /正在生成|生成中|停止生成|停止输出|正在回答|正在思考|思考中|正在分析/.test(body);
                let errText = '';
                if (/出错了|请求失败|网络错误|请重试|发生错误|服务异常|操作失败/.test(body)) {
                    errText = body.slice(0, 80);
                }
                let rejected = false;
                if (last) {
                    const t = (last.innerText || '').trim();
                    if (t.indexOf('我无法回答这个问题') >= 0) rejected = true;
                }
                return {len: len, count: mds.length, generating: generating, errText: errText, rejected: rejected};
            });
            lastState = st;
            if (st.rejected) {
                return {done: false, error: '千问风控拦截（回答为"我无法回答这个问题"），可能触发方式被识别，请稍后重试', state: st};
            }
            if (st.errText && st.len === 0) {
                return {done: false, error: st.errText, state: st};
            }
            if (st.count === 0) {
                zero += 1;
                if (zero >= 4) {
                    return {done: false, error: '页面没有回答（发送失败或页面状态异常）', state: st};
                }
            } else {
                zero = 0;
                if (st.generating) {
                    prevLen = st.len;
                    continue;
                }
                if (st.len > 0 && st.len === prevLen) {
                    stable += 1;
                    if (stable >= 2) {
                        return {done: true, state: st};
                    }
                } else {
                    stable = 0;
                }
            }
            prevLen = st.len;
        }
        return {done: false, state: lastState};
    }
    """ % (timeout_ms, int(round_secs * 1000))
    res = await client.call_tool("browser_run_code_unsafe", {"code": code})
    return parse_json_result(res)


async def extract_messages(client, keyword):
    """从页面抽取消息与引用链接（DOM 逻辑包在 page.evaluate 里）。"""
    code = """
    async (page) => {
        const kw = %s;
        const result = await page.evaluate((kw) => {
            const rounds = document.querySelectorAll('.chat-round');
            if (!rounds.length) {
                return {err: 'no chat-round', url: location.href, title: document.title};
            }
            const msgs = [];
            rounds.forEach((round) => {
                const q = round.querySelector('.message-card-wrap.question');
                const qText = q ? (q.innerText || '').trim() : '';
                if (qText) {
                    msgs.push({role: 'user', text: qText, thinking: '', links: []});
                }
                const md = round.querySelector('.qk-markdown');
                if (md) {
                    const text = (md.innerText || '').trim();
                    const links = [];
                    const seenLink = new Set();
                    round.querySelectorAll('a[href]').forEach((a) => {
                        const href = (a.href || '').trim();
                        if (href && href.indexOf('qianwen.com') < 0 && href.indexOf('tongyi.com') < 0 && !seenLink.has(href)) {
                            seenLink.add(href);
                            links.push({title: (a.innerText || '').trim(), href: href});
                        }
                    });
                    msgs.push({role: 'assistant', text: text, thinking: '', links: links});
                }
            });
            const hasUser = msgs.some(m => m.role === 'user');
            if (!hasUser && kw) {
                msgs.unshift({role: 'user', text: kw, thinking: '', links: []});
            }
            const assistants = msgs.filter(m => m.role === 'assistant');
            const last = assistants.length ? assistants[assistants.length - 1] : null;
            const best = {};
            if (last) {
                last.links.forEach((l) => {
                    const t = (l.title || '').replace(/[\\n-]/g, ' ').trim();
                    const title = /^\\d+$/.test(t) ? '' : t;
                    if (!best[l.href] || title.length > best[l.href].length) {
                        best[l.href] = title;
                    }
                });
            }
            const refs = Object.keys(best).map(h => ({title: best[h], href: h}));
            return {
                err: null,
                url: location.href,
                title: document.title,
                msgs: msgs,
                answer: last ? last.text : '',
                thinking: last ? last.thinking : '',
                references: refs
            };
        }, kw);
        return result;
    }
    """ % json.dumps(keyword, ensure_ascii=False)
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
    """执行一次千问搜索，返回结果 dict（含保存路径）。"""
    owns_client = client is None
    if owns_client:
        token = os.environ.get("PLAYWRIGHT_MCP_EXTENSION_TOKEN")
        client = PlaywrightMCP(token)
    now_iso = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result = {
        "platform": "qwen",
        "keyword": keyword,
        "queried_at": now_iso,
        "url": "",
        "title": "",
        "messages": [],
        "answer": "",
        "thinking": "",
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
        send_failed = bool(url and (url.startswith("SEND_FAIL") or url.startswith("### Error") or url.startswith("GOTO_ERR")))
        result["url"] = url or ""
        if send_failed:
            result["status"] = "error"
            result["error"] = "发送问题失败：%s" % str(url)[:200]
        else:
            wait_res = await wait_answer(client, wait_rounds, round_secs)
            if isinstance(wait_res, dict) and wait_res.get("error"):
                result["status"] = "error"
                result["error"] = wait_res["error"]
            elif isinstance(wait_res, dict) and not wait_res.get("done"):
                result["status"] = "error"
                result["error"] = "等待回答超时（%s 轮 x %s 秒）" % (wait_rounds, round_secs)
        extracted = await extract_messages(client, keyword)
        if isinstance(extracted, dict):
            if extracted.get("err"):
                result["status"] = "error"
                result["error"] = extracted["err"]
            result["url"] = extracted.get("url") or result["url"]
            result["title"] = extracted.get("title") or ""
            result["messages"] = extracted.get("msgs") or []
            result["answer"] = extracted.get("answer") or ""
            result["thinking"] = extracted.get("thinking") or ""
            result["references"] = extracted.get("references") or []
        if screenshot:
            shot = os.path.join(platform_dir("qwen"),
                                "qwen_%s_%s.png" % (safe_name(keyword), datetime.datetime.now().strftime("%H%M%S")))
            await save_screenshot(client, shot)
            result["screenshot"] = shot
        if not result["answer"]:
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
                await client.close_extension_tabs()
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
    parser = argparse.ArgumentParser(description="千问网页版 AI 搜索爬虫")
    parser.add_argument("keyword", nargs="?", help="搜索关键词/问题")
    parser.add_argument("--keyword", dest="keyword_opt", help="搜索关键词/问题（与位置参数二选一）")
    parser.add_argument("--wait-rounds", type=int, default=60, help="最长等待轮数（默认 60）")
    parser.add_argument("--round-secs", type=int, default=3, help="每轮等待秒数（默认 3）")
    parser.add_argument("--screenshot", action="store_true", help="同时保存页面截图")
    args = parser.parse_args()
    keyword = args.keyword or args.keyword_opt
    if not keyword:
        parser.error("请提供关键词，例如：python scripts/qwen.py \"什么是量子计算\"")
    result = asyncio.run(run(keyword, wait_rounds=args.wait_rounds, round_secs=args.round_secs, screenshot=args.screenshot))
    print_result(result)


if __name__ == "__main__":
    main()

