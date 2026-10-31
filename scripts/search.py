#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多平台 AI 搜索爬虫统一入口

用法：
  # 批量模式（推荐）：单连接 + 每个平台一张常驻 tab，关键词 -> 平台 顺序采集
  # 一个关键词轮询完所有平台后，再采集下一个关键词；换关键词复用同一张 tab（导航到新会话）
  # 当天已采集的 (关键词, 平台) 自动跳过；程序结束才统一关闭 tab
  python scripts/search.py --keywords "关键词1,关键词2"
  python scripts/search.py --keywords-file keywords.txt
  python scripts/search.py --platform doubao --keywords "关键词1,关键词2"   # 批量但只跑指定平台

  # 单平台单关键词模式（保留原功能）
  python scripts/search.py --platform doubao --keyword "关键词"
"""

import argparse
import asyncio
import datetime
import importlib
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import PlaywrightMCP, close_common_modals

# platform 名称 -> 实现模块（模块需提供 run(keyword, **kwargs, client=None) 返回结果 dict）
PLATFORMS = {
    "doubao": "doubao",
    "deepseek": "deepseek",
    "kimi": "kimi",
    "qwen": "qwen",
    "metaso": "metaso",
    "yuanbao": "yuanbao",
    "xiaohongshu": "xiaohongshu",
}

EXTRA_ARGS = {
    "doubao": ["wait_rounds", "round_secs", "screenshot"],
    "deepseek": ["wait_rounds", "round_secs", "screenshot"],
    "kimi": ["wait_rounds", "round_secs", "screenshot"],
    "qwen": ["wait_rounds", "round_secs", "screenshot"],
    "metaso": ["wait_rounds", "round_secs", "screenshot"],
    "yuanbao": ["wait_rounds", "round_secs", "screenshot"],
    "xiaohongshu": ["wait_rounds", "round_secs", "screenshot"],
}

# 各平台首页（创建常驻 tab 用）
PLATFORM_URLS = {
    "doubao": "https://www.doubao.com/chat/",
    "deepseek": "https://chat.deepseek.com/",
    "kimi": "https://www.kimi.com/",
    "qwen": "https://www.qianwen.com/",
    "metaso": "https://metaso.cn/",
    "yuanbao": "https://yuanbao.tencent.com/",
    "xiaohongshu": "https://www.xiaohongshu.com/ai_chat",
}

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.path.join(SKILL_ROOT, "data")


def parse_keywords(args):
    """合并 --keywords 与 --keywords-file，去重并保持顺序。"""
    kws = []
    if args.keywords:
        kws += [k.strip() for k in args.keywords.split(",") if k.strip()]
    if args.keywords_file:
        with open(args.keywords_file, "r", encoding="utf-8") as f:
            kws += [line.strip() for line in f if line.strip()]
    seen = set()
    out = []
    for k in kws:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def already_collected(date_str, platform, keyword):
    """判断今天该平台是否已有该关键词的成功记录（status=ok 且有答案）。"""
    pdir = os.path.join(DATA_ROOT, date_str, platform)
    if not os.path.isdir(pdir):
        return False
    kw_norm = keyword.strip()
    for fname in os.listdir(pdir):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(pdir, fname), "r", encoding="utf-8") as f:
                payload = json.load(f)
            if (payload.get("status") == "ok"
                    and (payload.get("answer") or "").strip()
                    and str(payload.get("keyword", "")).strip() == kw_norm):
                return True
        except Exception:
            continue
    return False


def blocked_reason(err):
    """从错误文本里识别"被限制/繁忙"类原因（验证码、拥挤排队、限流、额度耗尽）。"""
    for m in ["人机验证", "验证码", "CAPTCHA", "拥挤", "繁忙", "排队", "优先队列", "人太多", "BUSY", "限流", "额度", "用尽", "QUOTA"]:
        if m in err:
            return m
    return None


def is_blocked_error(err):
    return blocked_reason(err) is not None


def parse_current_tab_index(text):
    m = re.search(r"- (\d+): \(current\)", text or "")
    return int(m.group(1)) if m else None


async def call_tool(client, tool, args):
    res = await client.call_tool(tool, args)
    return res.content[0].text if res and res.content else str(res)


async def ensure_platform_tab(client, platform, tab_indices):
    """为该平台准备一张常驻 tab：已存在则 select，不存在则 new。返回 tab 索引。"""
    if platform in tab_indices:
        await call_tool(client, "browser_tabs", {"action": "select", "index": tab_indices[platform]})
        return tab_indices[platform]
    text = await call_tool(client, "browser_tabs", {"action": "new", "url": PLATFORM_URLS[platform]})
    idx = parse_current_tab_index(text)
    if idx is None:
        text2 = await call_tool(client, "browser_tabs", {"action": "list"})
        idx = parse_current_tab_index(text2)
    if idx is None:
        idx = 0
    tab_indices[platform] = idx
    return idx


async def close_platform_tabs(client, tab_indices):
    """批量结束后统一关闭各平台常驻 tab。

    必须按索引倒序关闭（先关最大索引），否则关闭后索引前移会关错/漏关；
    必须用 browser_tabs close（按索引直接关），browser_close(page.close) 会重建扩展 relay 页造成新 tab。"""
    for platform in sorted(tab_indices.keys(), key=lambda p: tab_indices[p], reverse=True):
        try:
            await call_tool(client, "browser_tabs", {"action": "close", "index": tab_indices[platform]})
        except Exception:
            pass


async def close_extension_relay_tabs(client):
    """关闭 Playwright MCP 扩展自带的 relay/欢迎页（chrome-extension:// 开头的 tab）。

    关闭全部常驻 tab 后，扩展会留下一个空白欢迎页（页面显示 "mcp connected."）。
    复用 base.PlaywrightMCP.close_extension_tabs 实现：列出所有 tab、识别扩展页并
    按索引倒序关闭，不影响普通网页 tab。"""
    try:
        await client.close_extension_tabs()
    except Exception:
        pass


async def collect_platform(client, platform, keyword, wait_rounds, round_secs, screenshot):
    mod = importlib.import_module(PLATFORMS[platform])
    # 全平台通用：采集前先关掉非验证码弹窗（会员/升级/通知等），
    # 内部实现会跳过验证码/受限类弹窗，不会误伤
    try:
        await close_common_modals(client)
    except Exception:
        pass
    return await mod.run(keyword=keyword, wait_rounds=wait_rounds, round_secs=round_secs,
                         screenshot=screenshot, client=client)


async def collect_with_retry_async(client, platform, keyword, wait_rounds, round_secs, screenshot):
    """在共享连接上采集；被限制/繁忙不重试；一般失败等 10 秒重试一次。"""
    result = await collect_platform(client, platform, keyword, wait_rounds, round_secs, screenshot)
    if result.get("status") != "ok":
        err = result.get("error") or ""
        if is_blocked_error(err):
            print("  [%s] 检测到%s（被限制/繁忙），跳过自动重试（请稍后或手动处理后重跑该关键词）" % (platform, blocked_reason(err)))
            return result, False
        print("  [%s] 首次失败（%s），10 秒后重试一次..." % (platform, err[:80]))
        await asyncio.sleep(10)
        result = await collect_platform(client, platform, keyword, wait_rounds, round_secs, screenshot)
        return result, True
    return result, False


async def run_batch_async(keywords, platforms, wait_rounds, round_secs, screenshot, dedup_fn=None, on_unit_done=None):
    """单连接批量：每平台一张常驻 tab，关键词 -> 平台 顺序采集；当天已采集跳过；结束统一关 tab。

    dedup_fn(date_str, platform, keyword) -> bool：自定义"当天已采集"判定；不传则用本地 data/ 文件判重。
    on_unit_done(result_dict) -> None：每个 (关键词, 平台) 出最终结果时回调（含 skipped/blocked），
    用于分布式 worker 向 Redis 上报；result_dict 至少含 platform/keyword/status/error/answer/references/url/queried_at。
    """
    token = os.environ.get("PLAYWRIGHT_MCP_EXTENSION_TOKEN")
    client = PlaywrightMCP(token)
    if not await client.connect():
        print("Playwright MCP 连接失败（请确认 Chrome 已启动且安装了 Playwright MCP 扩展）")
        return
    dedup_check = dedup_fn if dedup_fn is not None else already_collected
    date_str = datetime.date.today().isoformat()
    print("批量采集（单连接+每平台常驻tab）：%s 个关键词 x %s 个平台（日期 %s）" % (len(keywords), len(platforms), date_str))
    stats = {"collected": 0, "skipped": 0, "failed": 0, "blocked": 0}
    blocked_platforms = set()
    tab_indices = {}

    def emit(platform, kw, result):
        if on_unit_done is not None:
            try:
                on_unit_done(result)
            except Exception as e:
                print("  [%s] on_unit_done 回调异常：%s" % (platform, str(e)[:120]))

    try:
        for kw in keywords:
            print("\n===== 关键词：%s =====" % kw)
            for platform in platforms:
                if platform in blocked_platforms:
                    print("  [%s] 平台被限流/受限，跳过本批次其余关键词" % platform)
                    stats["blocked"] += 1
                    emit(platform, kw, {"platform": platform, "keyword": kw, "status": "blocked",
                                        "error": "平台被限流/受限，本批次其余关键词跳过",
                                        "answer": "", "references": [], "url": "", "queried_at": ""})
                    continue
                if dedup_check(date_str, platform, kw):
                    print("  [%s] 当天已采集，跳过" % platform)
                    stats["skipped"] += 1
                    emit(platform, kw, {"platform": platform, "keyword": kw, "status": "skipped",
                                        "error": "", "answer": "", "references": [], "url": "",
                                        "queried_at": ""})
                    continue
                await ensure_platform_tab(client, platform, tab_indices)
                print("  [%s] 开始采集（复用 tab=%s）..." % (platform, tab_indices[platform]))
                result, _ = await collect_with_retry_async(client, platform, kw, wait_rounds, round_secs, screenshot)
                if result.get("status") == "ok":
                    stats["collected"] += 1
                    print("  [%s] 成功 -> %s" % (platform, result.get("json_path")))
                else:
                    err = result.get("error") or ""
                    if is_blocked_error(err):
                        stats["blocked"] += 1
                        blocked_platforms.add(platform)
                        # 关键：受限单元必须归一到 blocked（平台脚本返回的是 error），
                        # 否则不计入终态，任务进度永远到不了 100%
                        result["status"] = "blocked"
                        print("  [%s] 失败（被限制/繁忙）：%s，本批次其余关键词跳过该平台" % (platform, err[:100]))
                    else:
                        stats["failed"] += 1
                        result["status"] = "failed"
                        print("  [%s] 失败：%s" % (platform, err[:120]))
                emit(platform, kw, result)
    finally:
        await close_platform_tabs(client, tab_indices)
        # 关闭扩展残留的 relay/欢迎页（mcp connected），避免留下多余 tab
        await close_extension_relay_tabs(client)
        try:
            await client.disconnect()
        except Exception:
            pass
    print("\n===== 批量采集完成：新增 %s 条，跳过 %s 条，失败 %s 条，受限跳过 %s 条 ====="
          % (stats["collected"], stats["skipped"], stats["failed"], stats["blocked"]))
    return stats


def run_single(platform, keyword, wait_rounds, round_secs, screenshot):
    """单平台单关键词采集（保留原功能，失败自动重试一次）。"""
    mod = importlib.import_module(PLATFORMS[platform])

    def collect():
        return asyncio.run(mod.run(keyword=keyword, wait_rounds=wait_rounds, round_secs=round_secs, screenshot=screenshot))

    result = collect()
    if result.get("status") != "ok":
        err = result.get("error") or ""
        if is_blocked_error(err):
            print("  [%s] 检测到%s（被限制/繁忙），跳过自动重试（请稍后或手动处理后重跑该关键词）" % (platform, blocked_reason(err)))
        else:
            print("  [%s] 首次失败（%s），10 秒后重试一次..." % (platform, err[:80]))
            time.sleep(10)
            result = collect()
    mod.print_result(result)


def main():
    parser = argparse.ArgumentParser(description="多平台 AI 搜索爬虫")
    parser.add_argument("--platform", default=None, choices=list(PLATFORMS.keys()),
                        help="目标平台；批量模式下若指定则只跑该平台")
    parser.add_argument("--keyword", help="单平台单关键词（保留原功能）")
    parser.add_argument("--keywords", help="批量关键词列表，逗号分隔")
    parser.add_argument("--keywords-file", help="关键词列表文件，每行一个")
    parser.add_argument("--wait-rounds", type=int, default=60, help="单次采集最长等待轮数（默认 60）")
    parser.add_argument("--round-secs", type=int, default=3, help="每轮等待秒数（默认 3）")
    parser.add_argument("--screenshot", action="store_true", help="同时保存页面截图")
    args = parser.parse_args()

    keywords = parse_keywords(args)
    if keywords:
        platforms = [args.platform] if args.platform else list(PLATFORMS.keys())
        asyncio.run(run_batch_async(keywords, platforms, args.wait_rounds, args.round_secs, args.screenshot))
    elif args.keyword:
        platform = args.platform or "doubao"
        run_single(platform, args.keyword, args.wait_rounds, args.round_secs, args.screenshot)
    else:
        parser.error("请提供 --keyword（单关键词）或 --keywords / --keywords-file（关键词列表）")


if __name__ == "__main__":
    main()

