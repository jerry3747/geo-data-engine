#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按天汇总各平台采集结果到 CSV

扫描 data/<日期>/<平台>/*.json，把成功采集的记录按 平台名称、关键词、答案 三列
写入 data/<日期>.csv（UTF-8 BOM，Excel 可直接打开）。跳过 status!=ok 或无答案的记录。

用法：
  python scripts/export_csv.py                    # 汇总今天
  python scripts/export_csv.py --date 2026-09-07  # 汇总指定日期
  python scripts/export_csv.py --out D:/tmp/xx.csv
"""

import argparse
import csv
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import PLATFORM_NAMES

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.path.join(SKILL_ROOT, "data")


def collect_rows(date_str):
    """读取某天 data/<日期>/<平台>/*.json，返回 (有效行列表, 跳过条数)。"""
    rows = []
    skipped = 0
    date_dir = os.path.join(DATA_ROOT, date_str)
    if not os.path.isdir(date_dir):
        return rows, skipped
    for platform_name in sorted(os.listdir(date_dir)):
        pdir = os.path.join(date_dir, platform_name)
        if not os.path.isdir(pdir):
            continue
        display = PLATFORM_NAMES.get(platform_name, platform_name)
        for fname in sorted(os.listdir(pdir)):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(pdir, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    payload = json.load(f)
            except Exception:
                skipped += 1
                continue
            keyword = payload.get("keyword", "")
            answer = (payload.get("answer") or "").strip()
            if payload.get("status") != "ok" or not answer:
                skipped += 1
                continue
            rows.append([display, keyword, answer])
    return rows, skipped


def export(date_str=None, out_path=None):
    """生成 CSV，返回 (csv 绝对路径, 有效记录数, 跳过条数)。"""
    if date_str is None:
        date_str = datetime.date.today().isoformat()
    rows, skipped = collect_rows(date_str)
    if out_path is None:
        out_path = os.path.join(DATA_ROOT, date_str + ".csv")
    out_dir = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["平台名称", "关键词", "答案"])
        writer.writerows(rows)
    return out_path, len(rows), skipped


def main():
    parser = argparse.ArgumentParser(description="按天汇总各平台采集结果到 CSV")
    parser.add_argument("--date", default=None, help="日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--out", default=None, help="CSV 输出路径，默认 data/<日期>.csv")
    args = parser.parse_args()
    path, count, skipped = export(args.date, args.out)
    print("CSV 已生成：%s" % path)
    print("有效记录：%s 条（跳过 %s 条无答案/失败记录）" % (count, skipped))


if __name__ == "__main__":
    main()
