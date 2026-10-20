---
name: geo-data-engine
description: Geo 数据引擎（多平台 AI 数据采集，豆包/DeepSeek/Kimi/千问/秘塔/元宝/小红书网页版）。用户提供关键词列表时，按"关键词→平台"顺序批量采集：每个关键词依次轮询完所有平台后再进入下一个关键词，当天已采集过的（关键词+平台）自动跳过、只补未采集的；也保留单平台单关键词单独采集。结果保存到 data/<日期>/<平台>/ 下的 JSON/Markdown，可一键按天汇总导出 CSV（平台名称/关键词/答案）。触发：用户要求"爬取豆包/DeepSeek/Kimi/千问/秘塔/元宝/小红书的搜索结果""AI 平台搜索""批量问 AI 搜索关键词""按关键词列表采集"等。
---

# geo-data-engine（Geo 数据引擎）

从七大 AI 平台**网页版**获取搜索/问答结果：用户给关键词，数据引擎在真实登录态下发起新会话提问，抽取回答与引用，落盘到 `data/`。

| 平台 | 脚本 | 状态 |
|---|---|---|
| 豆包 doubao.com | `scripts/doubao.py` | ✅ 已实现 |
| DeepSeek chat.deepseek.com | `scripts/deepseek.py` | ✅ 已实现 |
| Kimi kimi.com | `scripts/kimi.py` | ✅ 已实现 |
| 千问 qianwen.com | `scripts/qwen.py` | ✅ 已实现 |
| 秘塔 metaso.cn | `scripts/metaso.py` | ✅ 已实现 |
| 元宝 yuanbao.tencent.com | `scripts/yuanbao.py` | ✅ 已实现 |
| 小红书 xiaohongshu.com/ai_chat | `scripts/xiaohongshu.py` | ✅ 已实现 |

## 核心工作流（批量模式）

用户提供**关键词列表**时走批量模式：

1. 解析关键词列表（命令行逗号分隔，或每行一个关键词的文件，自动去重保持顺序）
2. **外层循环：关键词**；**内层循环：平台**——一个关键词依次采集完所有平台后，才进入下一个关键词
3. **单连接 + 每平台一张常驻 tab**：批量只建一个 MCP 连接，每个平台开一张 tab 并全程复用；换关键词时切回同一张 tab（导航到新会话），**不再频繁开/关页面**；全部结束后才统一关闭 tab
4. 每个 (关键词, 平台) 采集前先查当天 `data/<日期>/<平台>/` 是否已有**成功记录**（status=ok 且有答案）：
   - 已有 → 跳过，不重复采集
   - 没有 → 采集并落盘
5. 全部结束后打印汇总：新增 / 跳过 / 失败条数

## 调用方法

```bash
# 批量采集（推荐）：每个关键词轮询完所有平台，再采下一个关键词
python scripts/search.py --keywords "关键词1,关键词2"

# 关键词列表文件（每行一个关键词）
python scripts/search.py --keywords-file keywords.txt

# 批量但只跑指定平台（如只补豆包）
python scripts/search.py --platform doubao --keywords "关键词1,关键词2"

# 单平台单关键词（保留原功能）
python scripts/search.py --platform doubao --keyword "关键词"

# 控制等待时间 / 保存截图（批量与单平台通用）
python scripts/search.py --keywords "关键词" --wait-rounds 90 --round-secs 3 --screenshot
```

## 分布式采集模式（多台电脑并行，配合内置管理后台）

在每台爬虫电脑上运行项目根目录的 `worker.py`（项目入口），从 Redis 队列抢"关键词批次"并行采集；内置管理后台 `admin/` 负责建队、跟踪进度并持久化结果：

```bash
python worker.py            # 每台爬虫电脑上运行
python -m admin.main        # 管理后台（一个实例即可，默认 8843 端口）
```

- 配置：全部写在根目录 `worker_config.py`（参考根目录 `worker_config.example.py`）：`REDIS_URL` / `WORKER_ID` / `WAIT_ROUNDS` / `ROUND_SECS` / `POLL_TIMEOUT`，不支持命令行传参；MCP token 仍用环境变量 `PLAYWRIGHT_MCP_EXTENSION_TOKEN`
- 行为：批次内仍按 关键词→平台 串行、单连接 + 每平台常驻 tab；任务由管理后台在 Redis 建队，结果消息推 Redis，由后台内置消费者落 SQLite
- **分布式模式零本地落盘**（`base.ENABLE_LOCAL_SAVE=False`）：不写 json/md/png，判重以 Redis 全局集合为准（当天同平台同关键词全网只采一次，成功即标记）
- 管理后台 `admin/`（FastAPI + SQLite）：建任务、看整体进度、导出 CSV、重跑失败、重新入队未完成单元；在线机器列表由 worker 心跳（60s 过期）提供
- worker 崩溃恢复：重启时自动处理自己 `ai_search:processing:<worker_id>` 里的遗留批次；后台"重新入队"可兜底卡单

### 按天汇总导出 CSV

把当天（或指定日期）所有平台的成功结果汇总成一张 CSV，字段：`平台名称 / 关键词 / 答案`，文件以日期命名（UTF-8 BOM，Excel 可直接打开），跳过失败/无答案记录。

```bash
python scripts/export_csv.py                     # 汇总今天
python scripts/export_csv.py --date 2026-09-07   # 汇总指定日期
python scripts/export_csv.py --out D:/tmp/xx.csv # 自定义输出路径
```

## 采集顺序与去重规则

- **顺序**：关键词为外层、平台为内层。例：`--keywords "A,B"` 会按 A→豆包、A→DeepSeek、A→Kimi、A→千问、A→秘塔、A→元宝、B→豆包、B→DeepSeek……执行。
- **去重粒度**：(关键词, 平台)。同一天同一平台已有该关键词的成功记录（status=ok 且答案非空）即跳过；失败记录不算已采集，下次会重试。
- **去重范围**：按当天日期 `data/<日期>/` 判断，跨天重新采集。

## 输出结构

- `data/<日期>/<平台>/<平台>_<关键词>_<时间>.json`：结构化结果
- 同名 `.md`：可读版
- 可选 `.png` 截图（`--screenshot`）
- JSON 字段：`platform / keyword / queried_at / url / title / messages[{role,text,thinking,links}] / answer / references[{title,href}] / status / error`
- 秘塔额外有 `origins`（右侧来源标题列表）

## 环境前置（一次性）

1. Python 依赖：`pip install -r requirements.txt`（mcp、redis 等）
2. Chrome 已登录目标平台，并安装 Playwright MCP 扩展；token 用环境变量 `PLAYWRIGHT_MCP_EXTENSION_TOKEN`
3. 默认使用本地缓存的 `@playwright/mcp`（`PLAYWRIGHT_MCP_BIN` 可覆盖，不设则回退 `npx @playwright/mcp@latest --extension`）
4. 运行命令：Windows 用 `python`，macOS 用 `python3`

## 架构与扩展新平台

- `scripts/base.py`：PlaywrightMCP 客户端（连接/求值/导航/解析 MCP JSON 序列化）与按平台落盘、平台中文名映射，所有平台复用
- `scripts/search.py`：统一入口（批量/单平台），`PLATFORMS` 字典注册平台名→模块，`EXTRA_ARGS` 声明透传参数
- 新平台只需新建 `scripts/<platform>.py`，提供 `run(keyword, **kwargs) -> dict` 与 `print_result(result)`，并在 `search.py` 的 `PLATFORMS` 登记
- 各平台 DOM/踩坑记录在 `references/<platform>.md`；通用注意事项：`references/common.md`
- **关闭常驻 tab 的坑**：必须按索引**倒序**逐个 `browser_tabs close`（关闭后索引前移，正序会关错/漏关）；不要用 `browser_close`（page.close）——它会重建扩展 relay 页、制造新的扩展 tab

## 已知边界

- 依赖浏览器真实登录态，不处理登录、验证码、风控
- 不使用平台非公开 API，只采集页面展示的公开回答与引用
- 回答为 AI 生成内容，可能有误，保存时保留原样并提示"AI 生成可能有误"
- 批量采集是串行的（同一浏览器），关键词多时耗时较长，建议用 `--keywords-file` 分批


