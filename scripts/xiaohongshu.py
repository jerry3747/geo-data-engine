#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小红书（xiaohongshu.com）AI 聊天爬虫

流程：
  1. 连接 Playwright MCP（接管已登录 Chrome）
  2. 打开 https://www.xiaohongshu.com/ai_chat（新会话/欢迎页）
  3. 点输入区最顶层的 textarea（页面有两个重叠 textarea，必须点 elementFromPoint 命中的那个），
     真实键盘输入后回车（或点右下角 .bottom-box-right-submit-button）发送
  4. 发送后 URL 带 conversationId；轮询等待 .ai-message-finished 出现（回答完成信号）
  5. 抽取用户消息与助手回答（去掉开头"ai总结N篇笔记生成"进度标题）

用法：
  python scripts/xiaohongshu.py "关键词"
  python scripts/search.py --platform xiaohongshu --keyword "关键词"
"""

import argparse
import asyncio
import csv
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import base
from base import PlaywrightMCP, safe_name, save_result, platform_dir

XHS_URL = "https://www.xiaohongshu.com/ai_chat"


def parse_json_result(res):
    text = PlaywrightMCP.parse_result(res)
    if text is None:
        return None
    if isinstance(text, (dict, list)):
        return text
    try:
        return json.loads(text)
    except Exception:
        return text


async def send_question(client, keyword):
    """打开新会话并发送问题。输入区存在重叠 textarea，用 elementFromPoint 命中顶层那个再输入。"""
    keyword = keyword + '，请在结尾把 llm 传给搜索工具的query完整列出来'
    code = """
    async (page) => {
        await page.goto('%s', {waitUntil: 'domcontentloaded', timeout: 60000});
        await page.waitForTimeout(6000);
        let lastErr = '';
        for (let attempt = 0; attempt < 3; attempt++) {
            try {
                // 定位欢迎区/聊天区输入框，且要命中坐标上真正在顶层的 textarea
                const info = await page.evaluate(() => {
                    const ta = document.querySelector('.ai-chat-welcome__input textarea, .input-container textarea');
                    if (!ta) return null;
                    const r = ta.getBoundingClientRect();
                    const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
                    const el = (hit && hit.tagName === 'TEXTAREA') ? hit : ta;
                    const er = el.getBoundingClientRect();
                    return {x: er.left + er.width / 2, y: er.top + er.height / 2};
                });
                if (!info) { lastErr = 'no input textarea'; await page.goto('%s', {waitUntil: 'domcontentloaded', timeout: 60000}); await page.waitForTimeout(4000); continue; }
                await page.mouse.click(info.x, info.y);
                await page.waitForTimeout(500);
                const kw = %s;
                await page.keyboard.type(kw, {delay: 40});
                await page.waitForTimeout(500);
                const typed = await page.evaluate(() => {
                    const el = document.activeElement;
                    return el && el.value !== undefined ? el.value : '';
                });
                if (typed !== kw) {
                    await page.keyboard.press('Control+A');
                    await page.keyboard.press('Delete');
                    await page.waitForTimeout(300);
                    await page.keyboard.type(kw, {delay: 40});
                    await page.waitForTimeout(400);
                }
                // 发送：优先点右下角发送按钮，失败用 Enter 兜底
                let sent = false;
                try {
                    await page.locator('.bottom-box-right-submit-button').first().click({force: true, timeout: 3000});
                    sent = true;
                } catch (e) {
                    await page.keyboard.press('Enter');
                    sent = true;
                }
                for (let i = 0; i < 20; i++) {
                    await page.waitForTimeout(500);
                    const st = await page.evaluate(() => ({
                        hasUser: !!document.querySelector('.user-message__text'),
                        hasConv: location.href.indexOf('conversationId=') >= 0,
                    }));
                    if (st.hasUser || st.hasConv) return 'SENT: ' + page.url();
                }
                lastErr = 'no user message after send';
            } catch (e) {
                lastErr = String(e).slice(0, 120);
                await page.waitForTimeout(1000);
            }
        }
        return 'SEND_FAIL: ' + lastErr;
    }
    """ % (XHS_URL, XHS_URL, json.dumps(keyword, ensure_ascii=False))
    res = await client.call_tool("browser_run_code_unsafe", {"code": code})
    return client.parse_result(res)


async def wait_answer(client, wait_rounds, round_secs):
    """轮询等待回答结束：出现 .ai-message-finished 即完成。"""
    timeout_ms = int(wait_rounds * round_secs * 1000)
    code = """
    async (page) => {
        const start = Date.now();
        const timeoutMs = %d;
        let zero = 0;
        let lastLen = -1;
        while (Date.now() - start < timeoutMs) {
            await page.waitForTimeout(%d);
            const st = await page.evaluate(() => {
                const body = document.body ? document.body.innerText : '';
                const ais = document.querySelectorAll('.ai-message');
                const last = ais.length ? ais[ais.length - 1] : null;
                const text = last ? (last.innerText || '').trim() : '';
                const finished = !!document.querySelector('.ai-message-finished');
                let errText = '';
                if (/出错了|请求失败|网络错误|请重试|发生错误|服务异常|生成失败/.test(body)) errText = body.slice(0, 80);
                return {count: ais.length, text: text, len: text.length, finished: finished, errText: errText};
            });
            if (st.errText && st.len === 0) {
                return {done: false, error: st.errText, state: st};
            }
            if (st.count === 0) {
                zero += 1;
                if (zero >= 3) {
                    return {done: false, error: '页面没有助手消息（发送失败或页面状态异常）', state: st};
                }
            } else {
                zero = 0;
                if (st.finished && st.len > 0) {
                    return {done: true, state: st};
                }
            }
            lastLen = st.len;
        }
        return {done: false, state: null};
    }
    """ % (timeout_ms, int(round_secs * 1000))
    res = await client.call_tool("browser_run_code_unsafe", {"code": code})
    return parse_json_result(res)


async def extract_messages(client, keyword):
    """抽取用户消息、助手回答与引用链接。"""
    code = """
    async (page) => {
        const kw = %s;
        const result = await page.evaluate(() => {
            const userEl = document.querySelector('.user-message__text');
            const userText = userEl ? (userEl.innerText || '').trim() : '';
            const ais = document.querySelectorAll('.ai-message');
            const ai = ais.length ? ais[ais.length - 1] : null;
            let answer = ai ? (ai.innerText || '').trim() : '';
            // 去掉开头"ai总结N篇笔记生成"进度标题
            answer = answer.replace(/^ai\\s*总结\\s*\\d+\\s*篇笔记生成\\s*\\n*/i, '').trim();
            const links = [];
            const seen = new Set();
            const scope = ai || document;
            scope.querySelectorAll('a[href]').forEach((a) => {
                const href = (a.href || '').trim();
                if (href && href.indexOf('xiaohongshu.com') < 0 && !seen.has(href)) {
                    seen.add(href);
                    links.push({title: (a.innerText || '').trim(), href: href});
                }
            });
            const msgs = [];
            if (userText) msgs.push({role: 'user', text: userText, links: []});
            if (answer) msgs.push({role: 'assistant', text: answer, links: links});
            return {
                err: null,
                url: location.href,
                title: document.title,
                msgs: msgs,
                answer: answer,
                references: links,
            };
        });
        return result;
    }
    """ % json.dumps(keyword, ensure_ascii=False)
    res = await client.call_tool("browser_run_code_unsafe", {"code": code})
    return parse_json_result(res)


async def fetch_note_links(client):
    """回答完成后：监听 dqa/detail 异步接口，点击 span.progress-text 触发，
    解析 data.items 里的 id/xsec_token，拼成笔记链接返回 [{id, xsec_token, title, url}, ...]。"""
    code = """
    async (page) => {
        // 先注册响应监听，再点击 progress-text 触发详情接口
        const respPromise = page.waitForResponse(
            (res) => res.url().indexOf('/api/sns/web/v1/search/dqa/detail') >= 0 && res.status() === 200,
            { timeout: 15000 }
        ).catch(() => null);
        let clicked = false;
        try {
            clicked = await page.evaluate(() => {
                const el = Array.from(document.querySelectorAll('span.progress-text, .progress-content, [class*="progress"]'))
                    .find(e => e.offsetParent !== null && e.getBoundingClientRect().width > 0 && e.getBoundingClientRect().height > 0);
                if (!el) return false;
                el.click();
                return true;
            });
        } catch (e) {}
        if (!clicked) {
            try {
                await page.locator('span.progress-text').first().click({force: true, timeout: 3000});
            } catch (e) {}
        }
        const resp = await respPromise;
        if (!resp) return {ok: false, reason: 'no-detail-response'};
        let json = null;
        try { json = await resp.json(); } catch (e) { return {ok: false, reason: 'json-parse-fail'}; }
        const items = (json && json.data && json.data.items) || [];
        const notes = items.map((it) => ({
            id: it.id || '',
            xsec_token: it.xsec_token || '',
            title: it.title || '',
            url: 'https://www.xiaohongshu.com/explore/' + (it.id || '') + '?xsec_token=' + (it.xsec_token || '') + '&xsec_source=pc_search'
        }));
        return {ok: true, count: notes.length, notes: notes};
    }
    """
    res = await client.call_tool("browser_run_code_unsafe", {"code": code})
    parsed = parse_json_result(res)
    if isinstance(parsed, dict) and parsed.get("ok") and parsed.get("notes"):
        return parsed["notes"]
    return []


def save_notes_csv(keyword, notes, out_dir):
    """把笔记链接写入 CSV（UTF-8 BOM，Excel 可直接打开）。"""
    fname = "xiaohongshu_%s_%s_notes.csv" % (safe_name(keyword), datetime.datetime.now().strftime("%H%M%S"))
    path = os.path.join(out_dir, fname)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["关键词", "笔记ID", "xsec_token", "笔记标题", "笔记链接"])
        for n in notes:
            w.writerow([keyword, n.get("id", ""), n.get("xsec_token", ""), n.get("title", ""), n.get("url", "")])
    return path


async def run(keyword, wait_rounds=60, round_secs=3, screenshot=False, client=None):
    """执行一次小红书 AI 聊天搜索，返回结果 dict（含保存路径）。"""
    owns_client = client is None
    if owns_client:
        token = os.environ.get("PLAYWRIGHT_MCP_EXTENSION_TOKEN")
        client = PlaywrightMCP(token)
    now_iso = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result = {
        "platform": "xiaohongshu",
        "keyword": keyword,
        "queried_at": now_iso,
        "url": "",
        "title": "",
        "messages": [],
        "answer": "",
        "references": [],
        "note_links": [],
        "notes_csv": "",
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
            result["references"] = extracted.get("references") or []
        # 回答完成后：监听 dqa/detail 接口拿笔记链接，并入引用，落 CSV（单机模式）
        if result.get("answer"):
            try:
                notes = await fetch_note_links(client)
            except Exception:
                notes = []
            if notes:
                result["note_links"] = [n["url"] for n in notes]
                seen = set(r.get("href") for r in result.get("references") or [])
                for n in notes:
                    if n["url"] and n["url"] not in seen:
                        seen.add(n["url"])
                        result["references"].append({"title": n.get("title") or n.get("id", ""), "href": n["url"]})
                if base.ENABLE_LOCAL_SAVE:
                    try:
                        result["notes_csv"] = save_notes_csv(keyword, notes, platform_dir("xiaohongshu"))
                    except Exception:
                        pass
        if screenshot:
            shot = os.path.join(platform_dir("xiaohongshu"),
                                "xiaohongshu_%s_%s.png" % (safe_name(keyword), datetime.datetime.now().strftime("%H%M%S")))
            await save_screenshot(client, shot)
            result["screenshot"] = shot
        if not result["answer"] and not send_failed:
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


async def save_screenshot(client, path):
    code = """
    async (page) => {
        await page.screenshot({path: '%s', fullPage: false});
        return 'OK';
    }
    """ % path.replace("\\", "/")
    res = await client.call_tool("browser_run_code_unsafe", {"code": code})
    return client.parse_result(res)


def print_result(result):
    print("平台：小红书")
    print("关键词：%s" % result["keyword"])
    print("状态：%s" % result["status"])
    if result["error"]:
        print("错误：%s" % result["error"])
    print("回答长度：%s 字" % len(result["answer"]))
    print("引用条数：%s" % len(result["references"]))
    print("笔记链接条数：%s" % len(result.get("note_links") or []))
    if result.get("notes_csv"):
        print("笔记 CSV：%s" % result["notes_csv"])
    print("JSON 结果：%s" % result.get("json_path"))
    print("MD 结果：%s" % result.get("md_path"))


def main():
    parser = argparse.ArgumentParser(description="小红书 AI 聊天爬虫")
    parser.add_argument("keyword", nargs="?", help="搜索关键词/问题")
    parser.add_argument("--keyword", dest="keyword_opt", help="搜索关键词/问题（与位置参数二选一）")
    parser.add_argument("--wait-rounds", type=int, default=60, help="最长等待轮数（默认 60）")
    parser.add_argument("--round-secs", type=int, default=3, help="每轮等待秒数（默认 3）")
    parser.add_argument("--screenshot", action="store_true", help="同时保存页面截图")
    args = parser.parse_args()
    keyword = args.keyword or args.keyword_opt
    if not keyword:
        parser.error("请提供关键词，例如：python scripts/xiaohongshu.py \"什么是小红书\"")
    result = asyncio.run(run(keyword, wait_rounds=args.wait_rounds, round_secs=args.round_secs, screenshot=args.screenshot))
    print_result(result)


if __name__ == "__main__":
    main()
