# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Initial open-source release.
- Multi-platform AI search crawler for 7 platforms (Doubao / DeepSeek / Kimi / Qwen / Metaso / Yuanbao / Xiaohongshu) via their web UIs.
- Batch mode with keyword-first ordering and daily `(keyword, platform)` deduplication.
- Single MCP connection with one resident tab per platform.
- Daily CSV export (`scripts/export_csv.py`), UTF-8 BOM.
- Distributed worker mode (`worker.py`) with Redis queue, global dedup, heartbeats and crash recovery.
- Bilingual documentation (`README.md` / `README.zh-CN.md`), MIT license, contributing and security guidelines.
