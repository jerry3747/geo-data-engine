# GEO Now（单机版）

这是一个基于 [`jerry3747/geo-data-engine`](https://github.com/jerry3747/geo-data-engine) 采集思路改造、用于独立核验合作伙伴推广效果的本地 GEO 监测工具。第一阶段支持豆包和 Kimi 网页采集，并把每次回答、引用链接、时间、模式设置、截图和采集状态写入 SQLite；API 数据通过单独的导入入口保存为 `source=api`，报告会与网页数据分开统计。上游适配细节见 [`references/upstream.md`](references/upstream.md)。

## 快速开始

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[web]'
playwright install chromium
geonow init-config config.json
# 编辑 config.json：问题、品牌/别名、竞品、合作方文章 URL 和日期
geonow doctor
```

先做少量验证，确认页面、原始回答和截图都能保存：

```bash
geonow sample --config config.json --batch-id before-20261031-check \
  --provider all --samples-per-question 1 --limit 1 --headed --login-wait
geonow report --config config.json --output reports/check
```

如果当前机器尚未安装 Chromium 或暂时无法登录，可先用离线 fixture 验证 SQLite、截图路径、CSV 和报告链路（fixture 明确标记为离线结果，不代表真实网页数据）：

```bash
geonow sample --config config.json --batch-id fixture-check --provider all \
  --samples-per-question 1 --limit 1 --fixture-answer "fixture：推荐我的品牌"
geonow report --config config.json --output reports/fixture-check
```

通过验证后再扩大样本：

```bash
geonow sample --config config.json --batch-id before-20261031 \
  --provider all --samples-per-question 3 --headed --login-wait
```

每个样本都创建新的 Playwright browser context，不复用登录状态。遇到登录页时，使用 `--headed --login-wait`，在打开的浏览器中完成登录后回车；无交互环境会保存 `login_required` 失败样本。`--resume` 只跳过完全相同的批次/提供方/问题/采样编号，已移除按“同日成功”跳过的规则。重新采样请使用新的批次或采样编号，历史记录始终保留。

导入 API 结果并与网页数据分开：

```bash
geonow ingest-api --config config.json --file api.sample.json \
  --provider example-api-model --batch-id before-20261031-api
```

报告输出 CSV、中文 Markdown 和摘要 JSON：

```bash
geonow report --config config.json --output reports/all
```

报告按来源、提供方和批次统计品牌提及率、明确推荐率、合作方文章被引用率及竞品表现，显示有效样本数并单列失败样本。报告明确提示问题集、会话数、账号状态、地域、模型版本和随机性造成的采样限制；合作前后差异只作为时间相关性观察，不将全部变化直接归因于合作方。

## 数据目录

- `data/geonow.sqlite3`：历史记录，包含 `runs` 和 `samples`。
- `evidence/<batch>/<provider>/sample-XXXX.png`：每个样本的截图。
- `reports/`：CSV、Markdown 报告和摘要 JSON。

## Playwright MCP 兼容性

`geonow doctor` 检查 Python Playwright、Chromium，以及上游使用的 `PLAYWRIGHT_MCP_EXTENSION_TOKEN` / `PLAYWRIGHT_MCP_BIN`；若本机运行 MCP 服务，还可设置 `PLAYWRIGHT_MCP_URL=http://127.0.0.1:<port>` 做本地健康连通性检查。采集核心使用公开的 Playwright Python API，因此不依赖某个 MCP 服务器的专有工具名。页面选择器改变时，保留失败样本和截图，便于调整提供方配置或选择器。

## 测试

```bash
python -m unittest discover -s tests -v
```
