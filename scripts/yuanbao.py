#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""腾讯元宝（yuanbao.tencent.com）网页版 AI 搜索爬虫

流程：
  1. 连接 Playwright MCP（接管已登录 Chrome）
  2. 打开 https://yuanbao.tencent.com/（新会话），关掉"我知道了"弹窗
  3. 关键词输入 .ql-editor（Quill 编辑器）并回车发送
  4. 轮询等待回答完成（最后一段 .hyc-content-md 长度连续两轮不变，且无"处理中/生成中"状态）
  5. 抽取助手回答（.hyc-content-md）、用户消息与引用链接
  6. 保存到技能根目录 data/<日期>/yuanbao/yuanbao_<关键词>_<时间>.{json,md}

用法：
  python scripts/yuanbao.py "关键词"
  python scripts/search.py --platform yuanbao --keyword "关键词"
"""

import argparse
import asyncio
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import PlaywrightMCP, safe_name, save_result, platform_dir

YUANBAO_URL = "https://yuanbao.tencent.com/"
EDITOR_SEL = ".ql-editor"


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
    """打开元宝新会话，按需关闭弹窗并尽快发送问题。点击/输入/回车重试最多 3 次。"""
    keyword = keyword + '，请在结尾把 llm 传给搜索工具的 query、搜索召回的全部检索条目（网站名称 + 完整链接）完整列出来'
    code = """
    async (page) => {
        await page.goto('%s', {waitUntil: 'domcontentloaded', timeout: 60000});
        await page.waitForTimeout(3500);
        for (let i = 0; i < 4; i++) {
            const hasPop = await page.evaluate(() => {
                const els = Array.from(document.querySelectorAll('button, div, span'));
                const hit = els.find((el) => {
                    const t = (el.innerText || '').trim();
                    const r = el.getBoundingClientRect();
                    return t === '我知道了' && r.width > 10 && r.height > 10;
                });
                return !!hit;
            });
            if (!hasPop) break;
            try {
                await page.getByText('我知道了', {exact: true}).first().click({timeout: 1500});
            } catch (e) {}
            await page.waitForTimeout(300);
        }
        const ed = page.locator('%s');
        await ed.waitFor({state: 'attached', timeout: 20000});
        const kw = %s;
        let lastErr = '';
        for (let attempt = 0; attempt < 3; attempt++) {
            try {
                await ed.click({force: true, timeout: 8000});
                await ed.pressSequentially(kw, {delay: 20});
                const typed = (await ed.innerText()).trim();
                if (typed !== kw) {
                    lastErr = 'typed mismatch: ' + typed.slice(0, 30);
                    await page.keyboard.press('Control+A');
                    await page.keyboard.press('Delete');
                    await page.waitForTimeout(500);
                    continue;
                }
                await page.keyboard.press('Enter');
                for (let i = 0; i < 12; i++) {
                    await page.waitForTimeout(1000);
                    const hasMsg = await page.evaluate(() => document.body.innerText.indexOf(%s) >= 0);
                    if (hasMsg) return 'SENT';
                }
                lastErr = 'no message after send';
                await page.keyboard.press('Control+A');
                await page.keyboard.press('Delete');
                await page.waitForTimeout(500);
            } catch (e) {
                lastErr = String(e).slice(0, 120);
                await page.waitForTimeout(1500);
            }
        }
        return 'SEND_FAIL: ' + lastErr;
    }
    """ % (YUANBAO_URL, EDITOR_SEL, json.dumps(keyword, ensure_ascii=False), json.dumps(keyword, ensure_ascii=False))
    res = await client.call_tool("browser_run_code_unsafe", {"code": code})
    return client.parse_result(res)

async def wait_answer(client, wait_rounds, round_secs):
    """轮询等待回答结束。完成=停止按钮消失且最后一段 .hyc-content-md 带 done、长度连续两轮不变；
    带"思考超时/回答停滞"检测，避免生成卡死时干等满超时；Python 侧再加外层超时防挂起。"""
    timeout_ms = int(wait_rounds * round_secs * 1000)
    code = """
    async (page) => {
        const start = Date.now();
        const timeoutMs = %d;
        const roundMs = %d;
        let prevLen = -1;
        let stable = 0;
        let zero = 0;
        let thinkRounds = 0;
        let stallRounds = 0;
        let lastState = null;
        while (Date.now() - start < timeoutMs) {
            await page.waitForTimeout(roundMs);
            const st = await page.evaluate(() => {
                const body = document.body ? document.body.innerText : '';
                const mds = document.querySelectorAll('.hyc-content-md');
                const last = mds.length ? mds[mds.length - 1] : null;
                const len = last ? (last.innerText || '').trim().length : 0;
                const done = last ? (last.className || '').toString().indexOf('hyc-content-md-done') >= 0 : false;
                const hasStop = !!document.querySelector('[aria-label="停止回答"]');
                const generating = hasStop || /处理中|正在生成|生成中|停止生成|停止输出|正在思考|思考中/.test(body);
                let errText = '';
                if (/出错了|请求失败|网络错误|请重试|发生错误|服务异常/.test(body)) {
                    errText = body.slice(0, 80);
                }
                return {len: len, count: mds.length, done: done, generating: generating, errText: errText};
            });
            lastState = st;
            if (st.errText && st.len === 0) {
                return {done: false, error: st.errText, state: st};
            }
            if (st.count === 0) {
                if (st.generating) {
                    thinkRounds += 1;
                    if (thinkRounds >= 30) {
                        return {done: false, error: '长时间思考/检索无响应（已等待约 ' + Math.round(thinkRounds * roundMs / 1000) + ' 秒），请重试', state: st};
                    }
                } else {
                    zero += 1;
                    if (zero >= 4) {
                        return {done: false, error: '页面没有回答（发送失败或页面状态异常）', state: st};
                    }
                }
            } else {
                zero = 0;
                thinkRounds = 0;
                if (st.generating) {
                    if (st.len === prevLen) {
                        stallRounds += 1;
                        if (stallRounds >= 8) {
                            return {done: false, error: '回答停滞（约 ' + Math.round(stallRounds * roundMs / 1000) + ' 秒无进展），请重试', state: st};
                        }
                    } else {
                        stallRounds = 0;
                    }
                    prevLen = st.len;
                    continue;
                }
                if (st.len > 0 && st.len === prevLen && st.done) {
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
    try:
        res = await asyncio.wait_for(
            client.call_tool("browser_run_code_unsafe", {"code": code}),
            timeout=(wait_rounds * round_secs) + 120,
        )
    except asyncio.TimeoutError:
        return {"done": False, "error": "等待回答超时（浏览器无响应，已强制中断）", "state": None}
    return parse_json_result(res)

async def extract_messages(client, keyword):
    """从页面抽取消息与引用链接（DOM 逻辑包在 page.evaluate 里）。"""
    code = """
    async (page) => {
        const kw = %s;
        const result = await page.evaluate((kw) => {
            const items = document.querySelectorAll('.agent-chat__list__item__content');
            if (!items.length) {
                return {err: 'no chat item', url: location.href, title: document.title};
            }
            const msgs = [];
            items.forEach((item) => {
                const answerEls = item.querySelectorAll('.hyc-content-md');
                const answerEl = answerEls.length ? answerEls[answerEls.length - 1] : null;
                const userEl = item.querySelector('.hyc-content-text');
                if (answerEl) {
                    const text = (answerEl.innerText || '').trim();
                    const links = [];
                    const seenLink = new Set();
                    item.querySelectorAll('a[href]').forEach((a) => {
                        const href = (a.href || '').trim();
                        if (href && href.indexOf('yuanbao.tencent.com') < 0 && !seenLink.has(href)) {
                            seenLink.add(href);
                            links.push({title: (a.innerText || '').trim(), href: href});
                        }
                    });
                    msgs.push({role: 'assistant', text: text, thinking: '', links: links});
                } else if (userEl) {
                    const text = (userEl.innerText || '').trim();
                    if (text) msgs.push({role: 'user', text: text, thinking: '', links: []});
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
    """执行一次元宝搜索，返回结果 dict（含保存路径）。"""
    owns_client = client is None
    if owns_client:
        token = os.environ.get("PLAYWRIGHT_MCP_EXTENSION_TOKEN")
        client = PlaywrightMCP(token)
    now_iso = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result = {
        "platform": "yuanbao",
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
        await asyncio.sleep(2)
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
            shot = os.path.join(platform_dir("yuanbao"),
                                "yuanbao_%s_%s.png" % (safe_name(keyword), datetime.datetime.now().strftime("%H%M%S")))
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
    parser = argparse.ArgumentParser(description="腾讯元宝网页版 AI 搜索爬虫")
    parser.add_argument("keyword", nargs="?", help="搜索关键词/问题")
    parser.add_argument("--keyword", dest="keyword_opt", help="搜索关键词/问题（与位置参数二选一）")
    parser.add_argument("--wait-rounds", type=int, default=60, help="最长等待轮数（默认 60）")
    parser.add_argument("--round-secs", type=int, default=3, help="每轮等待秒数（默认 3）")
    parser.add_argument("--screenshot", action="store_true", help="同时保存页面截图")
    args = parser.parse_args()
    keyword = args.keyword or args.keyword_opt
    if not keyword:
        parser.error("请提供关键词，例如：python scripts/yuanbao.py \"什么是量子计算\"")
    result = asyncio.run(run(keyword, wait_rounds=args.wait_rounds, round_secs=args.round_secs, screenshot=args.screenshot))
    print_result(result)


if __name__ == "__main__":
    main()





