# 参与贡献

欢迎对本项目贡献代码、文档或 issue！请阅读以下指南。

## 开发流程

1. **Fork 本仓库**，基于 `master` 创建你的特性分支：`git checkout -b feature/my-feature`
2. 提交前确保：
   - 代码在 Python 3.10+ 上可运行；
   - 不引入新的依赖（除非必要并同步更新 `requirements.txt`）；
   - 不硬编码平台 DOM 结构（优先使用稳定的属性/前缀选择器）；
   - **绝不提交任何真实凭据**：`worker_config.py`（真实 Redis 配置）、token、密码、内网地址等。开发配置一律走 `worker_config.example.py` + 环境变量。
3. 提交信息清晰简洁，说明改动意图。
4. 提交 Pull Request，描述改动内容与测试情况。

## 新增平台

详见 README「扩展新平台」章节。核心要求：

- 新建 `scripts/<platform>.py`，实现 `run(keyword, **kwargs) -> dict` 与 `print_result(result)`；
- 在 `scripts/search.py` 的 `PLATFORMS`（以及必要的 `EXTRA_ARGS` / `PLATFORM_URLS`）注册，并同步 `admin/main.py` 的 `SUPPORTED_PLATFORMS`；
- 平台 DOM/踩坑记录写入 `references/<platform>.md`；
- 本地用 `python scripts/search.py --platform <platform> --keyword "测试"` 验证通过。

## 管理后台

`admin/` 是内置管理后台（FastAPI + SQLite + Redis），与 `worker.py` 共用 `worker_config.py` 的 `REDIS_URL`。改动时注意：

- Redis 队列/心跳/判重 key 必须与 `worker.py` 严格对齐（见 `admin/redis_client.py` 顶部注释）；
- SQLite 表结构变更需在 `admin/database.py` 的 `SCHEMA` 中同步（表由启动时 `init_db()` 自动创建）；
- Web UI 是 `admin/templates/index.html` 原生单页，新增功能需同时提供 API 与页面交互；
- 运行：`python -m admin.main`（默认 8843 端口）。

## 代码规范

- 遵循 PEP 8，保持现有代码风格；
- 中文注释优先（与现有代码一致）；
- 新功能需同步更新 `README.md`（中文默认）与 `README.en.md`（英文，中英双语保持一致）；
- 破坏性变更请记录到 `CHANGELOG.md`。

## 行为准则

- 尊重他人，保持专业；
- 聚焦技术讨论，就事论事；
- 本仓库不欢迎任何绕过登录墙、验证码或平台风控的"增强"请求——爬虫明确设计为只采集授权可访问的公开内容。
