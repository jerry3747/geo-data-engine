#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kimi（kimi.com）网页版 AI 搜索爬虫

流程：
  1. 连接 Playwright MCP（接管已登录 Chrome）
  2. 打开 https://www.kimi.com/（即新会话）
  3. 关键词输入 .chat-input-editor（Lexical 编辑器）并回车发送
  4. 轮询等待回答完成（.chat-content-item-assistant 文本长度连续两轮不变，且无"正在思考/生成"状态）
  5. 抽取助手回答（.markdown-container:not(.toolcall-content-text) .markdown）、思考过程与引用链接
  6. 保存到技能根目录 data/<日期>/kimi/kimi_<关键词>_<时间>.{json,md}

用法：
  python scripts/kimi.py "关键词"
  python scripts/search.py --platform kimi --keyword "关键词"
"""

import argparse
import asyncio
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import PlaywrightMCP, safe_name, save_result, platform_dir

KIMI_URL = "https://www.kimi.com/"
EDITOR_SEL = ".chat-input-editor"


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
    """打开新会话并发送问题。致命点：拥挤/额度弹窗出现后**绝不能再对页面做输入/点击**（会把它点掉，造成"一闪而过"）。
    任何输入/发送动作前先查弹窗标志，有就立即返回 BUSY/QUOTA 并保持弹窗原样；发送后没消息也不在同页重输，交给上层开新页重试。"""
    keyword = keyword + '，请在结尾把 llm 传给搜索工具的 query、搜索召回的全部检索条目（网站名称 + 完整链接）完整列出来'
    code = """
    async (page) => {
        const installObserver = async () => {
            await page.evaluate(() => {
                window.__kimiBusySeen = false;
                window.__kimiQuotaSeen = false;
                const findInChrome = (marks) => {
                    // 只扫聊天区/输入区之外的 UI 文案（排除 .chat-content-list 和 .chat-input*），
                    // 避免回答正文或输入框占位文案（"输入消息，按回车排队发送"）里的词被误判为弹窗。
                    const skipSel = '.chat-content-list, .chat-input-placeholder, .chat-input-editor, .chat-input-editor-container, .chat-editor-content, .chat-input';
                    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
                        acceptNode(node) {
                            const el = node.parentElement;
                            if (!el) return NodeFilter.FILTER_REJECT;
                            if (el.closest(skipSel)) return NodeFilter.FILTER_REJECT;
                            return NodeFilter.FILTER_ACCEPT;
                        }
                    });
                    let n;
                    while ((n = walker.nextNode())) {
                        const t = n.nodeValue || '';
                        for (const m of marks) {
                            if (t.indexOf(m) >= 0) return m;
                        }
                    }
                    return '';
                };
                const check = () => {
                    const quotaMarks = ['额度已用尽', '额度已用完', '额度用尽', '额度用完', '已用尽', '已用完', '额度不足', '配额不足', '套餐额度'];
                    if (findInChrome(['聊天的人太多', '人太多了', '优先队列', '排队', '服务器繁忙', '拥挤'])) window.__kimiBusySeen = true;
                    if (findInChrome(quotaMarks)) window.__kimiQuotaSeen = true;
                    // 兜底：直接扫弹窗容器（modal-container / modal-mask / role=dialog）的文本
                    if (!window.__kimiQuotaSeen) {
                        document.querySelectorAll('.modal-container, .modal-mask, [role="dialog"]').forEach((m) => {
                            const t = m.textContent || '';
                            for (const q of quotaMarks) {
                                if (t.indexOf(q) >= 0) { window.__kimiQuotaSeen = true; break; }
                            }
                        });
                    }
                };
                check();
                if (document.body) {
                    const mo = new MutationObserver(check);
                    mo.observe(document.body, {subtree: true, childList: true, characterData: true});
                    window.__kimiBusyMO = mo;
                }
            });
        };
        const resetObserver = async () => {
            await page.evaluate(() => {
                window.__kimiBusySeen = false;
                window.__kimiQuotaSeen = false;
            });
        };
        const busySeen = async () => {
            return await page.evaluate(() => window.__kimiBusySeen === true);
        };
        const quotaSeen = async () => {
            return await page.evaluate(() => window.__kimiQuotaSeen === true);
        };
        const blockedReturn = async () => {
            if (await quotaSeen()) return 'QUOTA: Kimi 额度已用尽/不足，请恢复额度后重试';
            if (await busySeen()) return 'BUSY: 和Kimi聊天的人太多了（弹窗存在，保持原样），订阅会员可进入优先队列，请稍后重试';
            return null;
        };
        const clearEditor = async (ed) => {
            for (let i = 0; i < 3; i++) {
                try {
                    await ed.click({force: true, timeout: 3000});
                } catch (e) {}
                await page.keyboard.press('Control+A');
                await page.keyboard.press('Delete');
                await page.waitForTimeout(400);
                const t = (await ed.innerText()).trim();
                if (!t) return true;
                try {
                    await ed.evaluate((el) => { el.innerText = ''; });
                    await page.waitForTimeout(400);
                } catch (e) {}
            }
            return (await ed.innerText()).trim() === '';
        };
        await page.goto('%s', {waitUntil: 'domcontentloaded', timeout: 60000});
        await page.waitForTimeout(6000);
        const ed = page.locator('%s');
        await ed.waitFor({state: 'attached', timeout: 30000});
        const kw = %s;
        await installObserver();
        // 聊天接口 HTTP >= 400 也算拥挤信号（流式响应体读不了，只看状态码）
        page.on('response', (res) => {
            if (res.url().indexOf('ChatService/Chat') < 0) return;
            const st = res.status();
            if (st >= 400) {
                window.__kimiChatErr = {status: st, snippet: 'http ' + st};
            }
        });
        let lastErr = '';
        for (let attempt = 0; attempt < 4; attempt++) {
            // 关键：任何输入/点击前先查弹窗，有就直接 BUSY/QUOTA，绝不动页面（否则会把弹窗点掉）
            { const b = await blockedReturn(); if (b) return b; }
            await resetObserver();
            try {
                await clearEditor(ed);
                { const b = await blockedReturn(); if (b) return b; }
                await ed.click({force: true, timeout: 5000});
                await ed.pressSequentially(kw, {delay: 40});
                const typed = (await ed.innerText()).trim();
                if (typed !== kw) {
                    lastErr = 'typed mismatch: ' + typed.slice(0, 40);
                    continue;
                }
                try {
                    await page.locator('.send-button-container').first().click({force: true, timeout: 5000});
                } catch (e) {
                    await page.keyboard.press('Enter');
                }
                let msgSeen = false;
                for (let i = 0; i < 30; i++) {
                    await page.waitForTimeout(250);
                    const apiErr = await page.evaluate(() => (window.__kimiChatErr ? JSON.stringify(window.__kimiChatErr).slice(0, 120) : null));
                    if (apiErr) {
                        return 'BUSY: Kimi 聊天接口返回 ' + apiErr + '，请稍后重试';
                    }
                    { const b = await blockedReturn(); if (b) return b; }
                    if (await page.evaluate(() => !!document.querySelector('.chat-content-item-assistant'))) {
                        msgSeen = true;
                        break;
                    }
                }
                if (msgSeen) return 'SENT: ' + page.url();
                lastErr = 'no message after send';
                // 发送后没消息：再查一次弹窗，有则不重试（避免同页重输把弹窗点掉）
                { const b = await blockedReturn(); if (b) return b; }
            } catch (e) {
                lastErr = String(e).slice(0, 120);
                await page.waitForTimeout(1200);
            }
        }
        { const b = await blockedReturn(); if (b) return b; }
        return 'SEND_FAIL: ' + lastErr;
    }
    """ % (KIMI_URL, EDITOR_SEL, json.dumps(keyword, ensure_ascii=False))
    res = await client.call_tool("browser_run_code_unsafe", {"code": code})
    return client.parse_result(res)

async def wait_answer(client, wait_rounds, round_secs):
    """轮询等待回答结束。完成=最后一条助手消息文本长度连续两轮不变且无"正在…"状态；
    检测 Kimi 拥挤弹窗（人太多/优先队列），持续拥挤明确报错。"""
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
                const items = document.querySelectorAll('.chat-content-item-assistant');
                const last = items.length ? items[items.length - 1] : null;
                const len = last ? (last.innerText || '').trim().length : 0;
                const generating = /正在思考|思考中|正在生成|生成中|停止生成|停止输出/.test(body);
                let errText = '';
                if (/出错了|请求失败|网络错误|请重试|发生错误|服务异常|连接已断开|重新生成|对话已失效/.test(body)) {
                    errText = body.slice(0, 80);
                }
                let busyText = '';
                let quotaText = '';
                // 只扫聊天区/输入区之外（排除 .chat-content-list 和 .chat-input*），
                // 回答正文或输入框占位文案出现"排队/拥挤/额度"等词不算弹窗
                {
                    const skipSel = '.chat-content-list, .chat-input-placeholder, .chat-input-editor, .chat-input-editor-container, .chat-editor-content, .chat-input';
                    const marks = ['聊天的人太多', '人太多了', '优先队列', '排队', '服务器繁忙', '拥挤'];
                    const quotaMarks = ['额度已用尽', '额度已用完', '额度用尽', '额度用完', '已用尽', '已用完', '额度不足', '配额不足', '套餐额度'];
                    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
                        acceptNode(node) {
                            const el = node.parentElement;
                            if (!el) return NodeFilter.FILTER_REJECT;
                            if (el.closest(skipSel)) return NodeFilter.FILTER_REJECT;
                            return NodeFilter.FILTER_ACCEPT;
                        }
                    });
                    let n;
                    while ((n = walker.nextNode())) {
                        const t = n.nodeValue || '';
                        for (const m of marks) {
                            if (t.indexOf(m) >= 0) { busyText = m; break; }
                        }
                        if (!busyText) {
                            for (const m of quotaMarks) {
                                if (t.indexOf(m) >= 0) { quotaText = m; break; }
                            }
                        }
                        if (busyText) break;
                    }
                    // 兜底：直接扫弹窗容器文本
                    if (!quotaText) {
                        document.querySelectorAll('.modal-container, .modal-mask, [role="dialog"]').forEach((m) => {
                            const t = m.textContent || '';
                            for (const q of quotaMarks) {
                                if (t.indexOf(q) >= 0) { quotaText = q; break; }
                            }
                        });
                    }
                }
                return {len: len, count: items.length, generating: generating, errText: errText, busyText: busyText, quotaText: quotaText};
            });
            lastState = st;
            if (st.quotaText) {
                return {done: false, error: 'Kimi 额度已用尽/不足（' + st.quotaText + '），请恢复额度后重试', state: st};
            }
            if (st.busyText) {
                return {done: false, error: 'Kimi 拥挤/繁忙（' + st.busyText + '），请稍后重试', state: st};
            }
            if (st.errText && st.len === 0) {
                return {done: false, error: st.errText, state: st};
            }
            if (st.count === 0) {
                if (st.generating) {
                    zero = 0;
                } else {
                    zero += 1;
                    if (zero >= 4) {
                        return {done: false, error: '页面没有助手消息（发送失败或页面状态异常）', state: st};
                    }
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
    """从页面抽取消息、思考过程与引用链接（DOM 逻辑包在 page.evaluate 里）。"""
    code = """
    async (page) => {
        const kw = %s;
        const result = await page.evaluate((kw) => {
            const items = document.querySelectorAll('.chat-content-item');
            if (!items.length) {
                return {err: 'no chat-content-item', url: location.href, title: document.title};
            }
            const msgs = [];
            items.forEach((item) => {
                const isUser = (item.className || '').toString().indexOf('chat-content-item-user') >= 0;
                const isAssistant = (item.className || '').toString().indexOf('chat-content-item-assistant') >= 0;
                if (isUser) {
                    const u = item.querySelector('.user-content');
                    const text = u ? (u.innerText || '').trim() : (item.innerText || '').trim();
                    msgs.push({role: 'user', text: text, thinking: '', links: []});
                } else if (isAssistant) {
                    const answerEl = item.querySelector('.markdown-container:not(.toolcall-content-text) .markdown');
                    const text = answerEl ? (answerEl.innerText || '').trim() : (item.innerText || '').trim();
                    const thinkEl = item.querySelector('.toolcall-content-text .markdown');
                    const thinkTitle = item.querySelector('.toolcall-title-name-text');
                    const thinkText = thinkEl ? (thinkEl.innerText || '').trim() : '';
                    const title = thinkTitle ? (thinkTitle.innerText || '').trim() : '';
                    const thinking = title ? title + '\\n' + thinkText : thinkText;
                    const links = [];
                    const seenLink = new Set();
                    item.querySelectorAll('a[href]').forEach((a) => {
                        const href = (a.href || '').trim();
                        if (href && href.indexOf('kimi.com') < 0 && !seenLink.has(href)) {
                            seenLink.add(href);
                            links.push({title: (a.innerText || '').trim(), href: href});
                        }
                    });
                    msgs.push({role: 'assistant', text: text, thinking: thinking, links: links});
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



async def ensure_page(client, expected_url):
    """页面守护：如果当前控制的页面不在 kimi.com 上（长跑/手动操作后 MCP 可能把页面重绑到扩展页），
    就重新导航回会话 URL，避免等待/抽取跑在错误的页面上。"""
    code = """
    async (page) => {
        const cur = await page.evaluate(() => location.href);
        if (cur.indexOf('kimi.com') >= 0) return 'OK';
        await page.goto('%s', {waitUntil: 'domcontentloaded', timeout: 60000});
        await page.waitForTimeout(4000);
        return page.url();
    }
    """ % expected_url
    res = await client.call_tool("browser_run_code_unsafe", {"code": code})
    return client.parse_result(res)

async def run(keyword, wait_rounds=60, round_secs=3, screenshot=False, client=None):
    """执行一次 Kimi 搜索，返回结果 dict（含保存路径）。"""
    owns_client = client is None
    if owns_client:
        token = os.environ.get("PLAYWRIGHT_MCP_EXTENSION_TOKEN")
        client = PlaywrightMCP(token)
    now_iso = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result = {
        "platform": "kimi",
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
        send_failed = bool(url and (url.startswith("SEND_FAIL") or url.startswith("BUSY") or url.startswith("QUOTA") or url.startswith("### Error") or url.startswith("GOTO_ERR")))
        busy_blocked = bool(url and (url.startswith("BUSY") or url.startswith("QUOTA")))
        result["url"] = url or ""
        chat_url = ""
        if url and url.startswith("SENT:"):
            chat_url = url.split("SENT:", 1)[1].strip()
        if send_failed:
            result["status"] = "error"
            if busy_blocked:
                result["error"] = str(url)
            else:
                result["error"] = "发送问题失败：%s" % str(url)[:200]
        else:
            if chat_url:
                await ensure_page(client, chat_url)
            wait_res = await wait_answer(client, wait_rounds, round_secs)
            if isinstance(wait_res, dict) and wait_res.get("error"):
                result["status"] = "error"
                result["error"] = wait_res["error"]
            elif isinstance(wait_res, dict) and not wait_res.get("done"):
                result["status"] = "error"
                result["error"] = "等待回答超时（%s 轮 x %s 秒）" % (wait_rounds, round_secs)
        extracted = None
        if not busy_blocked:
            if chat_url:
                await ensure_page(client, chat_url)
            extracted = await extract_messages(client, keyword)
        if extracted is not None and isinstance(extracted, dict) and extracted.get("err"):
            await asyncio.sleep(3)
            if chat_url:
                await ensure_page(client, chat_url)
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
            shot = os.path.join(platform_dir("kimi"),
                                "kimi_%s_%s.png" % (safe_name(keyword), datetime.datetime.now().strftime("%H%M%S")))
            await save_screenshot(client, shot)
            result["screenshot"] = shot
        if not result["answer"] and not busy_blocked:
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
    parser = argparse.ArgumentParser(description="Kimi 网页版 AI 搜索爬虫")
    parser.add_argument("keyword", nargs="?", help="搜索关键词/问题")
    parser.add_argument("--keyword", dest="keyword_opt", help="搜索关键词/问题（与位置参数二选一）")
    parser.add_argument("--wait-rounds", type=int, default=60, help="最长等待轮数（默认 60）")
    parser.add_argument("--round-secs", type=int, default=3, help="每轮等待秒数（默认 3）")
    parser.add_argument("--screenshot", action="store_true", help="同时保存页面截图")
    args = parser.parse_args()
    keyword = args.keyword or args.keyword_opt
    if not keyword:
        parser.error("请提供关键词，例如：python scripts/kimi.py \"什么是量子计算\"")
    result = asyncio.run(run(keyword, wait_rounds=args.wait_rounds, round_secs=args.round_secs, screenshot=args.screenshot))
    print_result(result)


if __name__ == "__main__":
    main()









