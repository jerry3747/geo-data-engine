# 上游仓库适配记录

本项目基于 `jerry3747/geo-data-engine`（读取时间：2026-11-01）。上游根目录的 `SKILL.md`、`README.md`、`scripts/base.py`、`scripts/doubao.py`、`scripts/kimi.py` 和 `scripts/search.py` 定义了这些可复用约定：

- 豆包和 Kimi 使用网页回答与页面引用，不调用平台非公开 API。
- 上游通过 `PlaywrightMCP`/Chrome 扩展接管真实浏览器，等待流式回答后保存 `answer`、`references`、状态和可选截图。
- 上游原本按“日期 + 关键词 + 平台”对成功样本去重，并复用平台 tab；这适合批量抓取，但不适合本工具的重复抽样和独立核验。

本单机版本保留网页采集和证据保留目标，做了针对监测的调整：

- 配置从关键词扩展为固定问题、品牌/别名、竞品和合作方文章 URL/日期。
- 每个样本通过新的 Playwright browser context 采集，使用 `batch_id + sample_no` 标识；仅 `--resume` 时跳过完全相同的样本身份，不再按同日成功跳过。
- 用 SQLite 历史库保存原始回答、可提取链接、模式设置、时间、状态和截图路径；API 导入使用 `source=api`，网页为 `source=web`。
- 保留上游 MCP 运行环境兼容性：`geonow doctor` 检查 Playwright/Chromium、`PLAYWRIGHT_MCP_EXTENSION_TOKEN`、`PLAYWRIGHT_MCP_BIN` 和可选 `PLAYWRIGHT_MCP_URL`。采集主路径使用公开 Playwright Python API，避免绑定 MCP 服务器的工具名和 tab 索引。

如果上游 DOM 选择器或引用结构变化，先复核上游对应脚本和本项目的失败截图，再调整 `geonow/collectors/web.py`；不要把失败页面当作有效回答。
