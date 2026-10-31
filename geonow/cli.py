from __future__ import annotations

import argparse
import json
import shutil
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .collectors.web import FixtureCollector, WebCollector, PROVIDER_DEFAULTS
from .models import MonitorConfig, load_config
from .reports import export_csv, write_report
from .storage import Store, now_iso


def _sample_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="config.example.json")
    parser.add_argument("--db", default="data/geonow.sqlite3")
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--provider", choices=["doubao", "kimi", "all"], default="all")
    parser.add_argument("--samples-per-question", type=int, default=1)
    parser.add_argument("--sample-start", type=int, default=1)
    parser.add_argument("--limit", type=int, help="每个提供方最多采集多少道题，建议首次验证使用 1-2")
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--login-wait", action="store_true", help="有登录页时在 headed 浏览器中等待你完成登录")
    parser.add_argument("--evidence-dir", default="evidence")
    parser.add_argument("--resume", action="store_true", help="仅跳过相同批次/提供方/问题/采样编号的已有样本")
    parser.add_argument("--fixture-answer", help="离线验证用固定回答；会保存 fixture 截图，但不代表真实网页采集")


def cmd_sample(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    providers = ["doubao", "kimi"] if args.provider == "all" else [args.provider]
    questions = [q for q in config.questions if q.enabled]
    if args.limit:
        questions = questions[:args.limit]
    store = Store(args.db)
    total = 0
    for provider in providers:
        provider_config = config.providers.get(provider, {})
        mode = {"source": "web", "provider": provider, "headless": args.headless,
                "samples_per_question": args.samples_per_question, "sample_start": args.sample_start,
                "config_path": str(Path(args.config).resolve())}
        run_id = store.new_run(args.batch_id, "web", provider, mode)
        collector = (FixtureCollector(args.fixture_answer, args.evidence_dir, provider)
                     if args.fixture_answer is not None else
                     WebCollector(provider, args.evidence_dir, args.headless, login_wait=args.login_wait,
                                  provider_config=provider_config))
        try:
            for question in questions:
                for offset in range(args.samples_per_question):
                    sample_no = args.sample_start + offset
                    if args.resume and store.has_identity(args.batch_id, provider, question.id, sample_no):
                        print(f"跳过已有样本 {provider}/{question.id}/#{sample_no}（--resume）")
                        continue
                    started = now_iso()
                    result = collector.collect(question.text, args.batch_id, sample_no)
                    store.insert_sample({
                        "run_id": run_id, "batch_id": args.batch_id, "sample_no": sample_no,
                        "question_id": question.id, "question_text": question.text,
                        "provider": provider, "source": "web", "started_at": started,
                        "finished_at": now_iso(), "status": result.status, "valid": result.valid,
                        "raw_answer": result.raw_answer, "answer_text": result.answer_text,
                        "citations": result.citations, "screenshot_path": result.screenshot_path,
                        "mode_settings": result.mode_settings, "error": result.error,
                    })
                    total += 1
                    print(f"{provider} {question.id} sample#{sample_no}: {result.status}")
            store.finish_run(run_id)
        except Exception:
            store.finish_run(run_id, "failed")
            raise
    store.close()
    print(f"完成 {total} 个样本；可用 geonow report 生成报告。")
    return 0


def cmd_ingest_api(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
    records = payload if isinstance(payload, list) else payload.get("samples", [])
    if not isinstance(records, list):
        raise ValueError("API 文件应为数组或 {samples: [...]} ")
    store = Store(args.db)
    run_id = store.new_run(args.batch_id, "api", args.provider, {"source": "api", "file": str(args.file)})
    for index, item in enumerate(records, 1):
        question_id = str(item.get("question_id") or f"api-{index}")
        qtext = str(item.get("question_text") or question_id)
        sample_no = int(item.get("sample_no", index))
        answer = str(item.get("answer_text") or item.get("raw_answer") or "")
        citations = item.get("citations") or item.get("links") or []
        status = str(item.get("status", "success" if answer else "empty"))
        valid = bool(item.get("valid", bool(answer and status == "success")))
        store.insert_sample({"run_id": run_id, "batch_id": args.batch_id, "sample_no": sample_no,
            "question_id": question_id, "question_text": qtext, "provider": args.provider, "source": "api",
            "started_at": item.get("started_at", now_iso()), "finished_at": item.get("finished_at", now_iso()),
            "status": status, "valid": valid, "raw_answer": item.get("raw_answer", answer),
            "answer_text": answer, "citations": citations, "screenshot_path": item.get("screenshot_path"),
            "mode_settings": item.get("mode_settings", {"source": "api"}), "error": item.get("error")})
    store.finish_run(run_id)
    store.close()
    print(f"已导入 {len(records)} 个 API 样本（source=api）。")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    store = Store(args.db)
    rows = store.rows("batch_id=?" if args.batch_id else "", [args.batch_id] if args.batch_id else [])
    if not rows:
        print("没有找到样本记录。", file=sys.stderr)
        return 2
    output = Path(args.output)
    export_csv(rows, output.with_suffix(".csv"))
    write_report(rows, config, output.with_suffix(".md"))
    store.close()
    print(f"CSV: {output.with_suffix('.csv')}\n报告: {output.with_suffix('.md')}\n有效样本: {sum(bool(r['valid']) for r in rows)} / {len(rows)}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    result: dict[str, Any] = {"python": sys.version.split()[0], "playwright": False, "chromium": False,
                              "mcp": {"configured": False, "reachable": False}}
    try:
        import playwright  # type: ignore
        result["playwright"] = True
        result["playwright_version"] = getattr(playwright, "__version__", "installed")
    except ImportError:
        result["playwright_error"] = "pip install -e '.[web]'"
    browser_path = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")
    if not browser_path and result["playwright"]:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
            with sync_playwright() as pw:
                candidate = pw.chromium.executable_path
                if Path(candidate).exists():
                    browser_path = candidate
        except Exception as exc:
            result["chromium_error"] = str(exc)
    result["chromium"] = bool(browser_path)
    result["chromium_path"] = browser_path
    mcp_url = __import__("os").environ.get("PLAYWRIGHT_MCP_URL")
    if mcp_url:
        result["mcp"]["configured"] = True
        try:
            with urllib.request.urlopen(mcp_url, timeout=2) as response:
                result["mcp"]["reachable"] = 200 <= response.status < 500
                result["mcp"]["status"] = response.status
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            result["mcp"]["error"] = str(exc)
    result["compatibility"] = "Playwright Python 直接控制浏览器；Playwright MCP 可通过 PLAYWRIGHT_MCP_URL 做连通性检查，本工具不依赖 MCP 专有协议。"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["playwright"] else 1


def cmd_init_config(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if path.exists() and not args.force:
        raise SystemExit(f"文件已存在：{path}；如需覆盖请加 --force")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"brand": "我的品牌", "aliases": [], "competitors": [],
        "questions": [{"id": "q1", "text": "在这个领域，哪些品牌值得优先考虑？"}],
        "partner_articles": [], "providers": PROVIDER_DEFAULTS,
        "sampling": {"samples_per_question": 3}}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已创建 {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="geonow", description="本地 GEO 监测工具（豆包/Kimi）")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init-config", help="创建配置模板")
    p.add_argument("path", nargs="?", default="config.json")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init_config)
    p = sub.add_parser("doctor", help="检查依赖、浏览器和 Playwright MCP 连通性")
    p.set_defaults(func=cmd_doctor)
    p = sub.add_parser("sample", help="采集网页样本")
    _sample_args(p); p.set_defaults(func=cmd_sample)
    p = sub.add_parser("ingest-api", help="导入 API 样本，保持与网页数据分开统计")
    p.add_argument("--file", required=True); p.add_argument("--config", default="config.example.json")
    p.add_argument("--db", default="data/geonow.sqlite3"); p.add_argument("--batch-id", required=True)
    p.add_argument("--provider", required=True); p.set_defaults(func=cmd_ingest_api)
    p = sub.add_parser("report", help="输出 CSV、中文报告和摘要 JSON")
    p.add_argument("--config", default="config.example.json"); p.add_argument("--db", default="data/geonow.sqlite3")
    p.add_argument("--batch-id"); p.add_argument("--output", default="reports/geonow")
    p.set_defaults(func=cmd_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
