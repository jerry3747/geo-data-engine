from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .metrics import summarize


def export_csv(rows: list[Any], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["sample_id", "batch_id", "sample_no", "question_id", "question_text", "provider", "source",
              "started_at", "finished_at", "status", "valid", "answer_text", "raw_answer", "citations_json",
              "screenshot_path", "mode_settings_json", "error"]
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))
    return path


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def write_report(rows: list[Any], config: Any, path: str | Path) -> Path:
    summary = summarize(rows, config.brand_terms, config.competitors, [a.url for a in config.partner_articles])
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    article_lines = "；".join(f"{a.title or a.url}（{a.published_at or '日期未配置'}）" for a in config.partner_articles) or "未配置"
    lines = ["# GEO 监测中文对比报告", "", f"生成时间：{__import__('datetime').datetime.now().isoformat(timespec='seconds')}", "",
             "## 监测配置", "", f"品牌：{config.brand}；别名：{'、'.join(config.aliases) or '无'}；竞品：{'、'.join(config.competitors) or '无'}", f"合作方文章：{article_lines}", "",
             "## 口径", "", "统计按数据来源（网页/API）、提供方和批次分别计算；分母是有效样本数，失败样本单列，不会混入比例。", ""]
    lines += ["## 批次与来源对比", "", "|来源|提供方|批次|样本数|有效样本|失败样本|品牌提及率|明确推荐率|合作方文章被引用率|", "|---|---|---|---:|---:|---:|---:|---:|---:|"]
    for g in summary["groups"]:
        lines.append(f"|{g['source']}|{g['provider']}|{g['batch_id']}|{g['sample_count']}|{g['valid_sample_count']}|{g['failed_sample_count']}|{_pct(g['brand_mention_rate'])}|{_pct(g['explicit_recommendation_rate'])}|{_pct(g['partner_article_citation_rate'])}|")
    lines += ["", "## 竞品表现", "", "|来源|提供方|批次|竞品|提及率|明确推荐率|", "|---|---|---|---|---:|---:|"]
    for g in summary["groups"]:
        for comp, stat in g["competitors"].items():
            lines.append(f"|{g['source']}|{g['provider']}|{g['batch_id']}|{comp}|{_pct(stat['mention_rate'])}|{_pct(stat['recommendation_rate'])}|")
    lines += ["", "## 批次变化（观察）", "", "同一来源和提供方按批次编号排序，下面的差值是描述性比较；若批次命名为 before/after，可直接作为合作前后观察线索。", "", "|来源|提供方|前批次|后批次|品牌提及率变化|明确推荐率变化|文章引用率变化|", "|---|---|---|---|---:|---:|---:|"]
    by_key = {}
    for g in summary["groups"]:
        by_key.setdefault((g["source"], g["provider"]), []).append(g)
    def delta(a: float | None, b: float | None) -> str:
        if a is None or b is None:
            return "—"
        return f"{(b-a)*100:+.1f}pp"
    def batch_sort_key(group: dict[str, Any]) -> tuple[int, str]:
        batch = str(group["batch_id"]).lower()
        # 约定常见的 before/after 批次先后；其它批次按编号排序。
        phase = 0 if batch.startswith(("before", "pre", "合作前")) else 1 if batch.startswith(("after", "post", "合作后")) else 2
        return phase, batch
    for (source, provider), groups in by_key.items():
        groups = sorted(groups, key=batch_sort_key)
        for before, after in zip(groups, groups[1:]):
            lines.append(f"|{source}|{provider}|{before['batch_id']}|{after['batch_id']}|{delta(before['brand_mention_rate'], after['brand_mention_rate'])}|{delta(before['explicit_recommendation_rate'], after['explicit_recommendation_rate'])}|{delta(before['partner_article_citation_rate'], after['partner_article_citation_rate'])}|")
    lines += ["", "## 解释与限制", "", "- 每个问题的重复采样都会使用新浏览器会话；批次和采样编号保存在样本记录中，历史记录不会被覆盖。", "- 失败、登录未完成、页面结构变化和空回答均单列；请优先检查截图和原始回答。", "- 这是有限问题集、有限时间和有限会话下的观测，结果受账号状态、地域、模型版本、随机性、页面改版和引用展示策略影响。", "- 合作前后差异只能作为时间相关性证据，不能把全部变化直接归因于合作方；应结合同期投放、品牌活动、价格、竞品动作和问题集变化解释。", "- 引用率只统计回答中可提取且与配置 URL 匹配的链接，无法证明模型实际采纳了文章内容。", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    path.with_suffix(".summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
