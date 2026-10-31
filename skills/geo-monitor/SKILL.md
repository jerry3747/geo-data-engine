---
name: geo-monitor
description: Run and extend a local GEO monitoring workflow for Chinese AI answer engines, currently Doubao and Kimi, with repeated Playwright samples, evidence capture, API/web separation, and Chinese comparison reports.
---

# GEO Monitor

Use this skill when a task needs repeatable local monitoring of AI answer engines for a configured brand, aliases, competitors, and partner article URLs.

## Operating rules

- Read the project `config.json` before collecting. Keep the question IDs stable so batches can be compared.
- Each sample must have a batch ID and sample number. A fresh Playwright browser context is required for every sample. Do not reintroduce a same-day-success deduplication rule.
- Preserve raw answer text, extracted citations, timestamps, mode settings, collection status, error text, and the screenshot path in SQLite. Failed samples remain visible and do not enter rate denominators.
- Keep `source=web` and `source=api` separate in metrics and reports. Use `geonow ingest-api` for API records.
- Start with `geonow sample --limit 1 --samples-per-question 1`; inspect screenshots and the raw records before expanding the batch.
- If a login page appears, use a headed browser with `--login-wait` and let the user complete login. Never silently claim a successful sample when login is incomplete.
- Reports must state sampling limits and describe before/after differences as observations with possible confounders, not proof that a partner caused all changes.

## Commands

```bash
geonow doctor
geonow sample --config config.json --batch-id <id> --provider all --samples-per-question 1 --limit 1 --headed --login-wait
geonow report --config config.json --output reports/<name>
```

For schema details and the API import shape, see the project [README](../../README.md) and [`api.sample.json`](../../api.sample.json).
