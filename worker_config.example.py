# -*- coding: utf-8 -*-
"""示例配置：分布式 worker 参数模板。

复制为根目录 worker_config.py 并填入你自己的配置，所有参数都从这里读取
（worker 不支持命令行传参）。另外必须设置环境变量
PLAYWRIGHT_MCP_EXTENSION_TOKEN（沿用现有爬虫的 MCP 扩展 token）。

注意：worker_config.py 已被 .gitignore 忽略，切勿提交真实凭据。
"""
import getpass

# Redis 连接串（flasky 后台与 worker 共用同一 Redis）。
# 格式：redis://[:password@]host:port/db
REDIS_URL = "redis://:YOUR_REDIS_PASSWORD@your.redis.host:6379/0"

# 本机 worker 标识（唯一即可，建议用电脑名，如 pc-01）
WORKER_ID = getpass.getuser()

# 单次采集最长等待轮数 / 每轮等待秒数（默认 60 / 3）
WAIT_ROUNDS = 60
ROUND_SECS = 0.1

# 队列空时阻塞等待秒数（默认 30）
POLL_TIMEOUT = 30
