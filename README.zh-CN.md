# geo-data-engine（Geo 数据引擎）

<div align="center">

**Geo 数据引擎** —— 通过 **网页版** 从 7 个 AI 平台（豆包 / DeepSeek / Kimi / 千问 / 秘塔 / 元宝 / 小红书）采集搜索/问答结果，支持基于 Redis 的**分布式多机并行采集**，并内置 **FastAPI + SQLite 管理后台**。

[English](README.md) · [简体中文](README.zh-CN.md)

</div>

---

## 目录

- [功能特性](#功能特性)
- [支持平台](#支持平台)
- [工作原理](#工作原理)
- [架构](#架构)
- [快速开始](#快速开始)
- [使用方法](#使用方法)
- [分布式采集模式（多机并行）](#分布式采集模式多机并行)
- [管理后台](#管理后台)
- [按天汇总导出 CSV](#按天汇总导出-csv)
- [输出结构](#输出结构)
- [扩展新平台](#扩展新平台)
- [已知边界](#已知边界)
- [免责声明与合规](#免责声明与合规)
- [开源协议](#开源协议)
- [参与贡献](#参与贡献)

## 功能特性

- **七平台、统一入口** —— 给定关键词列表即可批量查询所有平台，结果以结构化 JSON + 可读 Markdown 保存。
- **批量模式、关键词优先** —— 每个关键词依次轮询完所有平台后，再进入下一个关键词（关键词 → 平台）。
- **智能去重** —— 当天已成功采集的（关键词, 平台）自动跳过；失败记录不算已采集，下次会重试。
- **单连接 + 每平台常驻 tab** —— 全程复用一个 Playwright MCP 连接，每个平台开一张 tab 反复使用，全部结束后才统一关闭。
- **限流感知** —— 自动识别验证码 / 限流 / 繁忙状态，跳过自动重试并对该平台中止本批次剩余关键词。
- **按天汇总导出 CSV** —— 一键汇总当日全部成功结果（平台 / 关键词 / 答案），UTF-8 BOM，Excel 可直接打开。
- **分布式模式** —— 多台机器运行 `worker.py` 从 Redis 队列抢"关键词批次"，结果推回 Redis，本地零落盘。
- **内置管理后台** —— FastAPI + SQLite Web 界面：建任务、看进度、重跑失败、导出 CSV。
- **崩溃恢复** —— worker 重启自动处理自己的遗留批次；心跳让管理后台的在线机器列表保持准确。

## 支持平台

| 平台 | 脚本 | 地址 | 状态 |
|---|---|---|---|
| 豆包 | `scripts/doubao.py` | <https://www.doubao.com/chat/> | ✅ 已实现 |
| DeepSeek | `scripts/deepseek.py` | <https://chat.deepseek.com/> | ✅ 已实现 |
| Kimi | `scripts/kimi.py` | <https://www.kimi.com/> | ✅ 已实现 |
| 千问 | `scripts/qwen.py` | <https://www.qianwen.com/> | ✅ 已实现 |
| 秘塔 | `scripts/metaso.py` | <https://metaso.cn/> | ✅ 已实现 |
| 腾讯元宝 | `scripts/yuanbao.py` | <https://yuanbao.tencent.com/> | ✅ 已实现 |
| 小红书 | `scripts/xiaohongshu.py` | <https://www.xiaohongshu.com/ai_chat> | ✅ 已实现 |

## 工作原理

爬虫通过 [Playwright MCP](https://github.com/microsoft/playwright-mcp) 扩展接管**真实浏览器**（Chrome）。它复用你已登录的 Chrome，在各平台网页版打开新会话，用真实键盘输入关键词并回车发送，轮询等待回答流式结束，再抽取回答文本与引用链接。

```
关键词列表
   └─► 关键词#1 ─► 豆包 → DeepSeek → Kimi → 千问 → 秘塔 → 元宝 → 小红书
   └─► 关键词#2 ─► 豆包 → DeepSeek → Kimi → 千问 → 秘塔 → 元宝 → 小红书
   └─► ...
```

每个（关键词, 平台）单元采集前，先检查当天是否已有成功记录：

- **已有** → 跳过（去重，不重复采集）
- **没有** → 采集并落盘

## 架构

```
scripts/
├── base.py           # Playwright MCP 客户端（连接/求值/导航/解析），按平台落盘、
│                     #   平台中文名映射、通用弹窗关闭，所有平台复用
├── search.py         # 统一入口：批量/单平台，PLATFORMS 注册表，命令行参数
├── doubao.py         # 平台实现：run(keyword, **kwargs) -> dict
├── deepseek.py       # ...
├── kimi.py
├── qwen.py
├── metaso.py
├── yuanbao.py
├── xiaohongshu.py
└── export_csv.py     # 按天汇总导出 CSV
worker.py             # 分布式 worker 入口（Redis 队列 → 采集 → 上报 Redis）
admin/                # 内置管理后台（FastAPI + SQLite + Redis）
worker_config.example.py  # 配置模板 —— 复制为 worker_config.py 后填写（已 gitignore）
references/           # 各平台 DOM/踩坑记录（维护用文档）
data/                 # 采集结果，按天分目录（已 gitignore）
```

### 批量流程（单机）

```
        ┌──────────────────────────────┐
        │ scripts/search.py            │
        │  1. 解析关键词列表            │
        │  2. 连接 Playwright MCP      │
        │  3. 对每个关键词：            │
        │       对每个平台：            │
        │         当天已采集？          │──是──► 跳过
        │         │ 否                 │
        │         复用/常驻 tab        │
        │         发送 + 等待回答       │
        │         保存 json/md（+png） │
        │  4. 关闭 tab、断开连接        │
        └──────────────────────────────┘
```

## 快速开始

> **前置条件：** Python 3.10+；已安装 [Playwright MCP 扩展](https://chromewebstore.google.com/detail/playwright-extension/mmlmfjhmonkocbjadbfplnigmagldckm) 的 Chrome；目标平台已登录。

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 设置 MCP 扩展 token
#    Windows（PowerShell）:
$env:PLAYWRIGHT_MCP_EXTENSION_TOKEN = "your-token"
#    macOS / Linux:
export PLAYWRIGHT_MCP_EXTENSION_TOKEN="your-token"

# 3. 运行批量采集
python scripts/search.py --keywords "关键词1,关键词2"
```

> 扩展 token 从环境变量 `PLAYWRIGHT_MCP_EXTENSION_TOKEN` 读取。
> **安装扩展：** [Playwright 扩展 — Chrome 应用商店](https://chromewebstore.google.com/detail/playwright-extension/mmlmfjhmonkocbjadbfplnigmagldckm)（安装后扩展弹窗页会显示 MCP relay token）。
> 默认使用本地缓存的 `@playwright/mcp`；可用 `PLAYWRIGHT_MCP_BIN` 覆盖（不设置则回退 `npx @playwright/mcp@latest --extension`）。
> Windows 用 `python`，macOS/Linux 用 `python3`。

## 使用方法

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

### 命令行参数

| 参数 | 说明 |
|---|---|
| `--platform` | 目标平台；批量模式下指定则只跑该平台。可选：`doubao`、`deepseek`、`kimi`、`qwen`、`metaso`、`yuanbao`、`xiaohongshu` |
| `--keyword` | 单平台单关键词模式 |
| `--keywords` | 批量关键词列表，逗号分隔 |
| `--keywords-file` | 关键词列表文件，每行一个 |
| `--wait-rounds` | 单次采集最长等待轮数（默认 60） |
| `--round-secs` | 每轮等待秒数（默认 3） |
| `--screenshot` | 同时保存页面截图 |

## 分布式采集模式（多机并行）

在每台爬虫电脑上运行根目录 `worker.py`（项目入口），从 Redis 队列抢"关键词批次"并行采集；内置**管理后台**（`admin/`）负责建队、跟踪进度并持久化结果：

```bash
python worker.py            # 每台爬虫电脑上运行
python -m admin.main        # 管理后台（一个实例即可）
```

**配置** —— 全部写在根目录 `worker_config.py`（从 `worker_config.example.py` 复制；真实配置文件已被 gitignore，**切勿提交**）：

| 配置 | 说明 | 默认值 |
|---|---|---|
| `REDIS_URL` | Redis 连接串（必填，worker 与管理后台共用） | — |
| `WORKER_ID` | 本机 worker 标识 | 主机名-随机后缀 |
| `WAIT_ROUNDS` | 单次采集最长等待轮数 | 60 |
| `ROUND_SECS` | 每轮等待秒数 | 3 |
| `POLL_TIMEOUT` | 队列空时阻塞等待秒数 | 30 |

MCP token 仍用环境变量 `PLAYWRIGHT_MCP_EXTENSION_TOKEN`；worker 模式不支持命令行传参。

**行为说明**

- 批次内仍按 关键词 → 平台 串行、单连接 + 每平台常驻 tab。
- **分布式模式零本地落盘**（`base.ENABLE_LOCAL_SAVE = False`）：不写 json/md/png，判重以 Redis 全局集合为准（当天同平台同关键词全网只采一次）。
- 结果推入 Redis 结果队列，由管理后台内置消费者落 **SQLite**。
- 心跳（60s 过期）提供在线机器列表；worker 重启时自动处理自己的遗留批次；后台可"重新入队"兜底卡单。

## 管理后台

内置管理后台（`admin/`，FastAPI + SQLite + Redis）提供 Web 界面编排分布式采集，无需外部后台：

```
admin/
├── main.py          # FastAPI 应用：REST API + 静态页面
├── database.py      # SQLite 数据层（任务 / 单元）
├── redis_client.py  # Redis 队列 / 心跳 / 判重访问
├── consumer.py      # 后台消费者：结果队列 -> SQLite
└── templates/
    └── index.html   # 单页 Web 界面
```

**功能**

- **建任务** —— 粘贴关键词（每行一个）、**勾选**要采集的平台（不勾选 = 全部平台）；单元切分为批次入 Redis 队列，等 worker 领取采集。
- **进度跟踪** —— 任务与单元维度实时状态（pending / ok / failed / blocked / skipped）。
- **在线 worker** —— 从 Redis 心跳实时读取机器列表。
- **重跑失败** —— 把全部 failed/blocked 单元重置并重新入队；也可单个单元重排。
- **CSV 导出** —— 下载某任务全部成功单元为 UTF-8 BOM CSV。

**采集参数说明**

建任务时可调整采集行为：

| 参数 | 含义 | 默认值 |
|---|---|---|
| `wait_rounds` | 单次采集**最长等待轮数**。回答在 `wait_rounds × round_secs` 秒内未生成完毕，该单元判为 **failed（超时）**。 | 60 |
| `round_secs` | **每轮等待秒数**，即两次轮询检查的间隔。越小越快感知回答完成，但 DOM 轮询更频繁，更易触发平台风控。 | 0.1 |
| `chunk_size` | **每批单元数**，即每个 Redis 批次打包多少个（关键词×平台）单元。worker 一次领取一个批次；批次越小，进度/失败粒度越细、负载越均衡，批次越大则队列流量更少。 | 8 |

**启动**

```bash
pip install -r requirements.txt
python -m admin.main            # 或 uvicorn admin.main:app --host 0.0.0.0 --port 8843
```

然后访问 <http://localhost:8843>。后台从 `worker_config.py`（或 `REDIS_URL` 环境变量）读取 Redis 地址；未配置 Redis 时仍可启动并浏览任务，但队列/worker/建任务接口会返回明确提示。

## 按天汇总导出 CSV

把当天（或指定日期）所有平台的成功结果汇总成一张 CSV，字段：`平台名称 / 关键词 / 答案`，文件以日期命名（UTF-8 BOM，Excel 可直接打开），跳过失败/无答案记录。

```bash
python scripts/export_csv.py                     # 汇总今天
python scripts/export_csv.py --date 2026-09-07   # 汇总指定日期
python scripts/export_csv.py --out D:/tmp/xx.csv # 自定义输出路径
```

## 输出结构

结果保存在 `data/<日期>/<平台>/`：

```
data/
└── 2026-09-16/
    └── xiaohongshu/
        ├── xiaohongshu_<关键词>_<时间>.json   # 结构化结果
        ├── xiaohongshu_<关键词>_<时间>.md     # 可读版
        └── xiaohongshu_<关键词>_<时间>.png    # 可选截图（--screenshot）
```

**JSON 字段：** `platform / keyword / queried_at / url / title / messages[{role,text,thinking,links}] / answer / references[{title,href}] / status / error`
（秘塔额外有 `origins` —— 右侧来源标题列表。）

### 采集顺序与去重规则

- **顺序**：关键词为外层、平台为内层。例：`--keywords "A,B"` 会按 A→豆包、A→DeepSeek、A→Kimi、A→千问、A→秘塔、A→元宝、B→豆包、B→DeepSeek……执行。
- **去重粒度**：（关键词, 平台）。同一天同一平台已有该关键词的成功记录（status=ok 且答案非空）即跳过；失败记录不算已采集，下次会重试。
- **去重范围**：按当天日期 `data/<日期>/` 判断，跨天重新采集。

## 扩展新平台

1. 新建 `scripts/<platform>.py`，提供 `run(keyword, **kwargs) -> dict`（返回至少含 `status`、`answer` 的结果字典）与 `print_result(result)`。
2. 在 `scripts/search.py` 的 `PLATFORMS` 注册（可选 `EXTRA_ARGS`、`PLATFORM_URLS`）。
3. 复用 `base.py`：`PlaywrightMCP`、`save_result`、`safe_name`、`platform_dir`、`close_common_modals`。
4. 各平台 DOM/踩坑记录在 `references/<platform>.md`；通用注意事项在 `references/common.md`。

**关闭常驻 tab 的坑**：必须按索引**倒序**逐个 `browser_tabs close`（关闭后索引前移，正序会关错/漏关）；不要用 `browser_close`（page.close）——它会重建扩展 relay 页、制造新的扩展 tab。

## 已知边界

- 依赖浏览器真实登录态，不处理登录、验证码、风控。
- 不使用平台非公开 API，只采集页面展示的公开回答与引用。
- 回答为 AI 生成内容，可能有误，保存时保留原样并提示"AI 生成可能有误"。
- 批量采集是串行的（同一浏览器），关键词多时耗时较长，建议用 `--keywords-file` 分批。

## 免责声明与合规

- **仅供合法合规用途。** 只采集你有权访问的内容，并遵守各平台的服务条款、robots.txt 与限流要求；注意保护自己的账号。
- 爬虫使用真实登录态浏览器会话，**不绕过**登录墙、验证码与限流——这是设计使然，不是缺陷。
- 本软件按"原样"提供，不附带任何担保。使用者对使用方式及后果全权负责；作者与贡献者对使用本软件产生的任何损害不承担责任。
- AI 生成回答可能有误，重要信息请自行核实。

## 开源协议

本项目基于 [MIT 开源协议](LICENSE) 发布。

## 参与贡献

欢迎贡献！请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 了解贡献指南；发现安全问题请查看 [SECURITY.md](SECURITY.md)。变更记录见 [CHANGELOG.md](CHANGELOG.md)。
