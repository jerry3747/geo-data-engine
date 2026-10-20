<div align="center">

# 🌍 geo-data-engine

**Geo Data Engine** — a multi-platform AI data engine that collects search/Q&A answers from 7 AI platforms (Doubao / DeepSeek / Kimi / Qwen / Metaso / Yuanbao / Xiaohongshu) through their **web UIs**, with a Redis-based **distributed worker** mode and a built-in **FastAPI + SQLite** management dashboard.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white)
![Platforms](https://img.shields.io/badge/platforms-7%20AI%20Apps-8A2BE2)
![GitHub stars](https://img.shields.io/github/stars/jerry3747/geo-data-engine?style=social)

[English](README.md) · [简体中文](README.zh-CN.md)

</div>

---

## Table of Contents

- [Features](#features)
- [Supported Platforms](#supported-platforms)
- [How It Works](#how-it-works)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Usage](#usage)
- [Distributed Mode (Multi-Machine)](#distributed-mode-multi-machine)
- [Management Dashboard](#management-dashboard)
- [Export Results to CSV](#export-results-to-csv)
- [Output Structure](#output-structure)
- [Extending to a New Platform](#extending-to-a-new-platform)
- [Known Limitations](#known-limitations)
- [Disclaimer & Compliance](#disclaimer--compliance)
- [License](#license)
- [Contributing](#contributing)

## ✨ Features

- 🎯 **7 platforms, one interface** — query all platforms with a keyword list, results are saved as structured JSON + readable Markdown.
- 🔁 **Batch mode with keyword-first ordering** — for each keyword, iterate through all platforms before moving to the next keyword (keyword → platform).
- 🧠 **Smart deduplication** — `(keyword, platform)` pairs already collected successfully on the same day are automatically skipped; failed ones are retried.
- 🔌 **Single MCP connection with persistent tabs** — one Playwright MCP connection is reused, each platform keeps one resident tab for the whole batch; tabs are closed only at the end.
- 🛡️ **Rate-limit aware** — detects captcha / rate limiting / busy states, skips automatic retries and aborts the rest of the batch for that platform.
- 📈 **Daily CSV export** — one-click aggregation of all successful results per day (platform / keyword / answer), UTF-8 BOM so Excel opens it directly.
- 🖧 **Distributed mode** — run `worker.py` on multiple machines, grab keyword batches from a Redis queue, push results back to Redis; no local file writes.
- 🖥️ **Built-in management dashboard** — FastAPI + SQLite web UI for creating tasks, tracking progress, rerunning failures and exporting CSV.
- 💪 **Crash recovery** — workers automatically reprocess their leftover batches on restart; heartbeats keep the management UI online.

## 🤖 Supported Platforms

| Platform | Script | URL | Status |
|---|---|---|---|
| 豆包 Doubao | `scripts/doubao.py` | <https://www.doubao.com/chat/> | ✅ Implemented |
| DeepSeek | `scripts/deepseek.py` | <https://chat.deepseek.com/> | ✅ Implemented |
| Kimi | `scripts/kimi.py` | <https://www.kimi.com/> | ✅ Implemented |
| 千问 Qwen | `scripts/qwen.py` | <https://www.qianwen.com/> | ✅ Implemented |
| 秘塔 Metaso | `scripts/metaso.py` | <https://metaso.cn/> | ✅ Implemented |
| 腾讯元宝 Yuanbao | `scripts/yuanbao.py` | <https://yuanbao.tencent.com/> | ✅ Implemented |
| 小红书 Xiaohongshu | `scripts/xiaohongshu.py` | <https://www.xiaohongshu.com/ai_chat> | ✅ Implemented |

## ⚙️ How It Works

The crawler drives a **real browser** (Chrome) through the [Playwright MCP](https://github.com/microsoft/playwright-mcp) extension. It takes over your already-logged-in Chrome, opens a new conversation on each platform's web page, types the keyword with real keystrokes, waits for the streaming answer to finish, then extracts the answer text and reference links.

```
keyword list
   └─► keyword #1 ─► doubao → deepseek → kimi → qwen → metaso → yuanbao → xiaohongshu
   └─► keyword #2 ─► doubao → deepseek → kimi → qwen → metaso → yuanbao → xiaohongshu
   └─► ...
```

For every `(keyword, platform)` unit, the crawler first checks whether a successful record already exists for today:

- **Exists** → skip (deduplicated, not collected again)
- **Missing** → collect and save

## 🏗️ Architecture

```
scripts/
├── base.py           # Playwright MCP client (connect / evaluate / navigate / parse),
│                     #   per-platform save logic, Chinese platform-name mapping, modal closer
├── search.py         # Unified entry: batch / single-platform, PLATFORMS registry, CLI args
├── doubao.py         # Platform implementation: run(keyword, **kwargs) -> dict
├── deepseek.py       # ...
├── kimi.py
├── qwen.py
├── metaso.py
├── yuanbao.py
├── xiaohongshu.py
└── export_csv.py     # Daily aggregation to CSV
worker.py             # Distributed worker entry (Redis queue → crawl → report to Redis)
admin/                # Built-in management dashboard (FastAPI + SQLite + Redis)
worker_config.example.py  # Config template — copy to worker_config.py (gitignored)
references/           # Per-platform DOM / pitfall notes (maintenance docs)
data/                 # Crawled results, one directory per day (gitignored)
```

### Batch flow (single machine)

```
        ┌──────────────────────────────┐
        │ scripts/search.py            │
        │  1. parse keyword list       │
        │  2. connect Playwright MCP   │
        │  3. for each keyword:        │
        │       for each platform:     │
        │         already collected?   │──yes──► skip
        │         │ no                 │
        │         reuse/resident tab   │
        │         send + wait answer   │
        │         save json/md (+png)  │
        │  4. close tabs, disconnect   │
        └──────────────────────────────┘
```

## 🚀 Quick Start

> **Prerequisites:** Python 3.10+, Chrome with the [Playwright MCP extension](https://chromewebstore.google.com/detail/playwright-extension/mmlmfjhmonkocbjadbfplnigmagldckm) installed, and the target platforms logged in.

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set the MCP extension token
#    Windows (PowerShell):
$env:PLAYWRIGHT_MCP_EXTENSION_TOKEN = "your-token"
#    macOS / Linux:
export PLAYWRIGHT_MCP_EXTENSION_TOKEN="your-token"

# 3. Run a batch collection
python scripts/search.py --keywords "keyword1,keyword2"
```

> The extension token is read from the environment variable `PLAYWRIGHT_MCP_EXTENSION_TOKEN`.
> **Install the extension:** [Playwright Extension — Chrome Web Store](https://chromewebstore.google.com/detail/playwright-extension/mmlmfjhmonkocbjadbfplnigmagldckm) (the MCP relay token is shown on the extension's popup page after installation).
> By default the project uses a locally cached `@playwright/mcp` binary; set `PLAYWRIGHT_MCP_BIN` to override (otherwise it falls back to `npx @playwright/mcp@latest --extension`).
> Use `python` on Windows and `python3` on macOS/Linux.

## 📦 Usage

```bash
# Batch collection (recommended): each keyword is queried across all platforms
python scripts/search.py --keywords "keyword1,keyword2"

# Keywords from a file (one keyword per line)
python scripts/search.py --keywords-file keywords.txt

# Batch but only a specific platform (e.g. only Doubao)
python scripts/search.py --platform doubao --keywords "keyword1,keyword2"

# Single platform + single keyword (original behavior)
python scripts/search.py --platform doubao --keyword "keyword"

# Control waiting time / save screenshots (common to batch and single modes)
python scripts/search.py --keywords "keyword" --wait-rounds 90 --round-secs 3 --screenshot
```

### CLI Options

| Option | Description |
|---|---|
| `--platform` | Target platform. If set in batch mode, only that platform runs. Choices: `doubao`, `deepseek`, `kimi`, `qwen`, `metaso`, `yuanbao`, `xiaohongshu` |
| `--keyword` | Single keyword for single-platform mode |
| `--keywords` | Comma-separated keyword list for batch mode |
| `--keywords-file` | File with one keyword per line |
| `--wait-rounds` | Max polling rounds while waiting for an answer (default `60`) |
| `--round-secs` | Seconds per polling round (default `3`) |
| `--screenshot` | Also save a page screenshot |

## 🌐 Distributed Mode (Multi-Machine)

Run `worker.py` on every crawler machine — workers pull keyword batches from a Redis queue and crawl in parallel. The built-in **management dashboard** (`admin/`) owns the queue, tracks progress and persists results:

```bash
python worker.py            # on every crawler machine
python -m admin.main        # the management dashboard (one instance)
```

**Configuration** — everything is read from `worker_config.py` at the project root (copy it from `worker_config.example.py`; the real file is gitignored and must never be committed):

| Config | Description | Default |
|---|---|---|
| `REDIS_URL` | Redis connection string (required, shared by worker & admin) | — |
| `WORKER_ID` | Unique worker id for this machine | hostname-random |
| `WAIT_ROUNDS` | Max waiting rounds per collection | `60` |
| `ROUND_SECS` | Seconds per round | `3` |
| `POLL_TIMEOUT` | Blocking wait (s) when the queue is empty | `30` |

The MCP token still comes from the environment variable `PLAYWRIGHT_MCP_EXTENSION_TOKEN`. CLI arguments are not supported in worker mode.

**Behavior**

- Within a batch, execution is still keyword → platform serial, with a single connection and one resident tab per platform.
- **Zero local writes in distributed mode** (`base.ENABLE_LOCAL_SAVE = False`): no json/md/png files; deduplication is based on a Redis global set (each `(platform, keyword)` is collected once per day across all machines).
- Results are pushed to the Redis result queue and persisted to **SQLite** by the admin's built-in consumer.
- Heartbeats (60s expiry) feed the online-machine list; workers handle their leftover batches on restart; the dashboard can re-queue stuck units.

## 🖥️ Management Dashboard

The built-in dashboard (`admin/`, FastAPI + SQLite + Redis) gives you a web UI to orchestrate distributed collection — no external backend required:

```
admin/
├── main.py          # FastAPI app: REST API + static UI
├── database.py      # SQLite layer (tasks / units)
├── redis_client.py  # Redis queue / heartbeat / dedup access
├── consumer.py      # background consumer: result queue -> SQLite
└── templates/
    └── index.html   # single-page web UI
```

**Features**

- **Create tasks** — paste keywords (one per line) and tick the platforms to collect (default: all if none ticked); units are split into batches and enqueued to Redis, waiting for workers to pick them up.
- **Progress tracking** — per-task and per-unit status (pending / ok / failed / blocked / skipped), real-time.
- **Online workers** — live machine list from Redis heartbeats.
- **Rerun failures** — reset all failed/blocked units and re-enqueue them; or re-enqueue a single unit.
- **CSV export** — download all successful units of a task as a UTF-8 BOM CSV.

**Collection parameters**

When creating a task you can tune how the crawl behaves:

| Parameter | Meaning | Default |
|---|---|---|
| `wait_rounds` | Max polling rounds while waiting for a single answer. If the answer is not finished within `wait_rounds × round_secs` seconds, the unit is marked **failed** (timeout). | `60` |
| `round_secs` | Seconds between two polling checks. Lower = faster detection of a finished answer, but more frequent DOM polling (higher risk of triggering rate limits). | `0.1` |
| `chunk_size` | How many `(keyword, platform)` units are packed into one Redis batch. A worker grabs one batch at a time; smaller batches give finer progress/failure granularity and better load balancing, larger batches reduce queue traffic. | `8` |

**Start**

```bash
pip install -r requirements.txt
python -m admin.main            # or: uvicorn admin.main:app --host 0.0.0.0 --port 8843
```

Then open <http://localhost:8843>. The dashboard reads `REDIS_URL` from `worker_config.py` (or the `REDIS_URL` env var); without Redis it still starts and you can browse tasks, but queue/worker/creation endpoints return a clear message.

## 📊 Export Results to CSV

Aggregate all successful results of a day (or a given date) into one CSV with columns `平台名称 / 关键词 / 答案` (platform / keyword / answer). The file is named by date and written with a UTF-8 BOM so Excel opens it directly. Failed / empty records are skipped.

```bash
python scripts/export_csv.py                     # today
python scripts/export_csv.py --date 2026-09-07   # a specific date
python scripts/export_csv.py --out D:/tmp/xx.csv # custom output path
```

## 📁 Output Structure

Results are saved under `data/<date>/<platform>/`:

```
data/
└── 2026-09-16/
    └── xiaohongshu/
        ├── xiaohongshu_<keyword>_<time>.json   # structured result
        ├── xiaohongshu_<keyword>_<time>.md     # human-readable
        └── xiaohongshu_<keyword>_<time>.png    # optional screenshot (--screenshot)
```

**JSON fields:** `platform / keyword / queried_at / url / title / messages[{role,text,thinking,links}] / answer / references[{title,href}] / status / error`
(Metaso additionally has `origins` — the right-side source-title list.)

### Deduplication Rules

- **Order:** keyword is the outer loop, platform the inner. E.g. `--keywords "A,B"` runs A→doubao, A→deepseek, A→kimi, A→qwen, A→metaso, A→yuanbao, B→doubao, B→deepseek, …
- **Granularity:** `(keyword, platform)`. A successful record (status=ok with a non-empty answer) for the same platform and keyword on the same day is skipped; failed records are not counted and will be retried.
- **Scope:** per calendar day, based on the `data/<date>/` directory; a new day starts fresh.

## 🔌 Extending to a New Platform

1. Create `scripts/<platform>.py` implementing `run(keyword, **kwargs) -> dict` (returns the result dict with at least `status` and `answer`) and `print_result(result)`.
2. Register it in the `PLATFORMS` dict (and optionally `EXTRA_ARGS`, `PLATFORM_URLS`) in `scripts/search.py`.
3. Reuse `base.py`: `PlaywrightMCP`, `save_result`, `safe_name`, `platform_dir`, `close_common_modals`.
4. Keep per-platform DOM / pitfall notes in `references/<platform>.md`; general pitfalls live in `references/common.md`.

**Resident-tab closing gotcha:** tabs must be closed by index in **descending** order (indexes shift after each close, so forward order closes the wrong / misses tabs); do not use `browser_close` (page.close) — it rebuilds the extension relay page and creates a new extension tab.

## ⚠️ Known Limitations

- Depends on real browser login state; login, CAPTCHA and risk control are not handled.
- Does not use non-public platform APIs — only answers and references displayed on the page are collected.
- Answers are AI-generated content and may be wrong; they are saved as-is with a "may contain errors" note.
- Batch collection is serial (one browser); many keywords take a long time — use `--keywords-file` to split into batches.

## ⚖️ Disclaimer & Compliance

- **For legal & ethical use only.** Only collect content you are authorized to access, and respect the terms of service, robots.txt and rate limits of each platform. Be mindful of your accounts.
- The crawler uses real logged-in browser sessions and does **not** bypass login walls, CAPTCHA or rate limiting. This is a feature, not a bug.
- The software is provided "as is", without warranty of any kind. You are solely responsible for how you use it and for any consequences. The authors and contributors are not liable for any damages arising from the use of this software.
- AI-generated answers may contain errors; verify important information before relying on it.

## 📄 License

This project is licensed under the [MIT License](LICENSE).

## 🤝 Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines, and check [SECURITY.md](SECURITY.md) if you find a security issue. Changes are tracked in [CHANGELOG.md](CHANGELOG.md).
