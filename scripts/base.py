#!/usr/bin/env python3
"""
Playwright MCP 客户端类 - 支持双模式连接
同时支持上下文管理器模式和手动连接模式
"""

import asyncio
import csv
import datetime
import json
import os
import re
import traceback

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def timestamp_to_datetime(timestamp: int) -> str:
    """
    将时间戳转换为日期时间字符串
    :param timestamp: 时间戳（秒）
    :return: 格式化的日期时间字符串
    """
    return datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')


def append_to_csv(data: list, filename: str, headers: list) -> str:
    """
    将数据追加到 CSV 文件
    如果文件不存在则创建并写入表头

    Args:
        data: 数据列表（每行为一个字典）
        filename: 文件名
        headers: CSV 表头列表

    Returns:
        str: CSV 文件绝对路径
    """
    file_dir = os.path.join(os.path.expanduser("~"), ".chrome_profile", "tmp")
    if not os.path.exists(file_dir):
        os.makedirs(file_dir)

    file_path = os.path.join(file_dir, filename)
    file_exists = os.path.exists(file_path)

    with open(file_path, 'a', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        if not file_exists:
            writer.writeheader()
        for row in data:
            writer.writerow(row)

    print(f"数据已写入: {file_path}")
    return file_path


class PlaywrightMCP:
    """
    Playwright MCP 客户端类

    支持两种使用模式：
    1. 上下文管理器模式: async with PlaywrightMCP() as client:
    2. 手动控制模式: client = PlaywrightMCP(); await client.connect(); await client.disconnect()

    两种模式都能确保正确的资源管理和连接清理
    """

    def __init__(self, token):
        """
        初始化客户端

        Args:
            token: MCP服务器访问令牌（必须）

        Raises:
            ValueError: 当 token 为空或 None 时抛出
        """
        if not token:
            raise ValueError(
                "必须提供 token 参数\n"
                "请通过以下方式之一配置：\n"
                "1. 在 .env 文件中设置 PLAYWRIGHT_MCP_EXTENSION_TOKEN\n"
                "2. 或显式传入 token 参数"
            )
        self.token = token
        self._connected = False
        self._stdio_context = None
        self._session = None
        self._stdio_exit = None
        self._session_exit = None
        self.file_dir = os.path.join(os.path.expanduser("~"), ".chrome_profile", "tmp")

    async def connect(self):
        """
        手动连接到MCP服务器

        Returns:
            bool: 连接是否成功

        使用示例：
        client = PlaywrightMCP()
        if await client.connect():
            # 使用客户端
            await client.disconnect()
        """
        if self._connected:
            print("已经连接到MCP服务器")
            return True

        try:
            print("正在手动连接MCP服务器...")

            # 创建服务器参数
            server_params = StdioServerParameters(
                command=os.environ.get("PLAYWRIGHT_MCP_BIN") or "npx",
                args=(["@playwright/mcp@latest"] if not os.environ.get("PLAYWRIGHT_MCP_BIN") else []) + ["--extension"],
                env={
                    "PLAYWRIGHT_MCP_EXTENSION_TOKEN": self.token
                }
            )

            # 获取stdio客户端
            self._stdio_context = stdio_client(server_params)

            # 使用正确的嵌套上下文管理器模式
            self._stdio_exit = self._stdio_context.__aenter__()
            read_stream, write_stream = await self._stdio_exit

            # 创建ClientSession
            self._session = ClientSession(read_stream, write_stream)
            self._session_exit = self._session.__aenter__()
            await self._session_exit

            # 初始化会话
            print("正在初始化会话...")
            await self._session.initialize()
            print("MCP服务器连接成功!")

            # 标记为已连接
            self._connected = True
            return True
        except Exception as e:
            print(f"连接MCP服务器失败: {e}")
            # 确保在失败时也清理资源
            await self._cleanup()
            return False

    async def get_tools(self):
        """
        获取可用工具列表

        Returns:
            list: 工具列表
        """
        if not self._session or not self._connected:
            print("未连接到MCP服务器")
            return []

        try:
            response = await self._session.list_tools()
            return response.tools
        except Exception as e:
            print(f"获取工具列表失败: {e}")
            return []

    async def call_tool(self, tool_name, arguments=None):
        """
        调用指定工具

        Args:
            tool_name: 工具名称
            arguments: 工具参数

        Returns:
            工具执行结果
        """
        if not self._session or not self._connected:
            print("未连接到MCP服务器")
            return None

        try:
            if arguments is None:
                arguments = {}

            result = await self._session.call_tool(
                name=tool_name,
                arguments=arguments
            )
            return result
        except Exception as e:
            traceback.print_exc()
            print(f"[失败] 调用工具失败: {e}")
            # 更新连接状态，确保 _ensure_connected() 能正确触发重连
            self._connected = False
            return None

    async def disconnect(self):
        """
        手动断开连接并清理资源

        Returns:
            bool: 断开是否成功

        使用示例：
        client = PlaywrightMCP()
        await client.connect()
        # 使用客户端
        await client.disconnect()
        """
        if not self._connected:
            print("未连接到MCP服务器")
            return True

        print("正在断开MCP服务器连接...")
        await self._cleanup()
        print("MCP服务器已断开连接")
        return True

    async def _cleanup(self):
        """
        内部方法：清理所有资源
        按相反的顺序关闭所有打开的上下文管理器
        """
        # 首先关闭会话上下文
        if self._session_exit is not None:
            try:
                await self._session.__aexit__(None, None, None)
                print("ClientSession已关闭")
            except Exception as e:
                print(f"关闭ClientSession时发生错误: {e}")
            finally:
                self._session_exit = None

        # 然后关闭stdio上下文
        if self._stdio_exit is not None:
            try:
                await self._stdio_context.__aexit__(None, None, None)
                print("stdio上下文已关闭")
            except Exception as e:
                print(f"关闭stdio上下文时发生错误: {e}")
            finally:
                self._stdio_exit = None

        # 重置所有状态
        self._connected = False
        self._session = None
        self._stdio_context = None

    # 覆盖__aenter__和__aexit__以支持异步上下文管理器协议
    async def __aenter__(self):
        """
        异步上下文管理器入口
        """
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """
        异步上下文管理器出口
        """
        await self.disconnect()

    async def evaluate(self, fn: str) -> str | None:
        """在页面上下文执行函数字符串；fn 形如 '() => {...}' 或 'async () => {...}'。"""
        res = await self.call_tool("browser_evaluate", {"function": fn})
        return self.parse_result(res)

    async def run_code(self, code: str) -> str | None:
        """运行可访问 page 的代码字符串；code 形如 'async (page) => {...}'。"""
        res = await self.call_tool("browser_run_code_unsafe", {"code": code})
        return self.parse_result(res)

    @staticmethod
    def parse_result(result) -> str | None:
        """从 MCP 工具返回里提取结果文本，并还原 JSON 序列化。

        Playwright MCP 会把返回值做 JSON 序列化：普通字符串会带引号（如
        '"true"'），对象/数组会整体被编码。这里统一 json.loads 还原成干净的
        字符串，避免调用方拿 '"true"' != 'true' 之类的坑。
        """
        if result is None or not getattr(result, "content", None):
            return None
        raw = result.content[0].text
        if not raw:
            return None
        m = re.search(r"### Result\n(.*?)\n###", raw, re.S)
        text = (m.group(1).strip() if m else raw.strip())
        if text is None:
            return None
        # 尝试还原 MCP 的 JSON 序列化（带引号字符串 / 转义后的对象）
        try:
            decoded = json.loads(text)
            return decoded if isinstance(decoded, str) else json.dumps(decoded, ensure_ascii=False)
        except Exception:
            return text

    # ----------------------------------------------------------
    # 通用方法（BOSS 打招呼 recommend.py / 沟通自动回复 chat.py 共用）
    # ----------------------------------------------------------
    async def wait_for_selector(self, selector: str, timeout: int = 30) -> bool:
        """轮询等待主 document 中的选择器出现。"""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            res = await self.evaluate("() => { return !!document.querySelector('%s'); }" % selector)
            if res == "true":
                return True
            await asyncio.sleep(1)
        return False

    async def handel_api_response(self, api, wait_timeout_ms: int = 30000):
        """等待单次接口响应并返回其 JSON。

        注意：page.on('response') 持续监听在本环境不可靠（实测不触发），
        必须用 waitForResponse 每次注册、操作后取响应。
        超时返回 None（不抛错，由调用方判断为"没有新数据"）。
        """
        get_json_code = f"""
                async (page) => {{
                    try {{
                        const exportResponse = await page.waitForResponse(
                            (response) => {{
                                const url = response.url();
                                const status = response.status();
                                return status === 200 && url.includes('{api}');
                            }},
                            {{ timeout: {int(wait_timeout_ms)} }}
                        );
                        const data = await exportResponse.json();
                        return data;
                    }} catch (e) {{
                        return null;
                    }}
                }}
                """
        json_result = await self.call_tool(
            "browser_run_code_unsafe",
            {"code": get_json_code}
        )
        return json_result

    def parse_mcp_json(self, result):
        """从 browser_run_code_unsafe 的返回里提取 JSON（兼容 ### Result 包裹格式）。"""
        if result is None or not getattr(result, "content", None):
            return None
        raw_text = result.content[0].text
        if not raw_text:
            return None
        m = re.search(r"### Result\s*(\{.*\})\s*### Ran Playwright code", raw_text)
        if not m:
            return None
        json_text = m.group(1)
        try:
            return json.loads(json_text)
        except json.JSONDecodeError:
            return None

    async def goto(self, url: str, timeout_ms: int = 60000) -> str | None:
        """导航当前标签页到 url，返回最终 URL 或错误文本。"""
        code = """
        async (page) => {
            try {
                await page.goto('%s', {waitUntil: 'domcontentloaded', timeout: %d});
                return page.url();
            } catch (e) {
                return 'GOTO_ERR:' + String(e).slice(0, 200);
            }
        }
        """ % (url, timeout_ms)
        return await self.run_code(code)

    async def close_tab(self) -> str:
        """关闭当前 Playwright 控制的标签页（tab）。结束后调用，避免留下浏览器页面。"""
        code = """
        async (page) => {
            try {
                await page.close();
                return 'TAB_CLOSED';
            } catch (e) {
                return 'CLOSE_FAIL:' + String(e).slice(0, 100);
            }
        }
        """
        res = await self.run_code(code)
        return res or "CLOSE_FAIL"

    async def close_extension_tabs(self):
        """关闭 Playwright MCP 扩展自带的 relay/欢迎页（chrome-extension:// 开头的 tab）。

        关闭常驻 tab（或 page.close）后，扩展会留下一个空白欢迎页（显示 "mcp connected."）。
        列出所有 tab，识别扩展页并按索引倒序关闭；普通网页（http/https）不受影响。"""
        try:
            text = await self.call_tool("browser_tabs", {"action": "list"})
        except Exception:
            return
        raw = (text.content[0].text if text and getattr(text, "content", None) else None) or str(text)
        ext_indices = []
        for line in str(raw).splitlines():
            m = re.match(r"^\s*(\d+):\s+(\S+)", line)
            if not m:
                continue
            idx, url = int(m.group(1)), m.group(2)
            if url.startswith("chrome-extension://") or "chrome-extension" in url:
                ext_indices.append(idx)
        if not ext_indices:
            return
        print("  [cleanup] 关闭 %d 个 Playwright MCP 扩展页（倒序）..." % len(ext_indices))
        for idx in sorted(ext_indices, reverse=True):
            try:
                await self.call_tool("browser_tabs", {"action": "close", "index": idx})
            except Exception:
                pass


# ----------------------------------------------------------
# 通用：按平台落盘（data/<日期>/<平台>/<平台>_<关键词>_<时间>.{json,md}）
# ----------------------------------------------------------
PLATFORM_NAMES = {
    "doubao": "豆包",
    "deepseek": "DeepSeek",
    "kimi": "Kimi",
    "qwen": "千问",
    "metaso": "秘塔",
    "yuanbao": "元宝",
    "xiaohongshu": "小红书",
}


def safe_name(text):
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t ]+', "_", str(text or ""))
    return cleaned.strip("_")[:40] or "kw"


# 全局落盘开关：分布式 worker 启动时会置为 False，从此不创建目录、不写 json/md/png，
# 结果全部走 Redis 中转；单机命令行模式保持 True，行为与原来完全一致。
ENABLE_LOCAL_SAVE = True


def platform_dir(platform, date_str=None):
    """返回并按需创建 data/<日期>/<平台> 目录（相对本技能根目录）。"""
    if date_str is None:
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", date_str, platform)
    os.makedirs(d, exist_ok=True)
    return d


def save_result(payload):
    """按平台保存结构化 JSON + 可读 Markdown，返回 (json_path, md_path)。"""
    if not ENABLE_LOCAL_SAVE:
        return None, None
    now = datetime.datetime.now()
    platform = payload.get("platform", "unknown")
    out_dir = platform_dir(platform, now.strftime("%Y-%m-%d"))
    fname = "%s_%s_%s" % (platform, safe_name(payload.get("keyword", "")), now.strftime("%H%M%S"))
    json_path = os.path.join(out_dir, fname + ".json")
    md_path = os.path.join(out_dir, fname + ".md")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    display = PLATFORM_NAMES.get(platform, platform)
    answer = payload.get("answer") or ""
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# %s 搜索结果\n\n" % display)
        f.write("- 平台：%s网页版\n" % display)
        f.write("- 关键词：%s\n" % payload.get("keyword", ""))
        f.write("- 查询时间：%s\n" % payload.get("queried_at", ""))
        f.write("- 会话链接：%s\n" % payload.get("url", ""))
        f.write("\n## 回答\n\n%s\n\n" % answer)
        refs = payload.get("references") or []
        if refs:
            f.write("## 引用来源\n\n")
            for i, r in enumerate(refs, 1):
                title = r.get("title") or r.get("href")
                f.write("%d. [%s](%s)\n" % (i, title, r.get("href")))
    return json_path, md_path


async def close_common_modals(client):
    """全平台通用：关闭非验证码弹窗（会员/升级/通知/登录提示等），供各平台采集前调用。

    安全策略（绝不误伤验证码/受限弹窗）：
      1. 页面整体开头文本命中"验证码/人机/额度/拥挤/排队"等关键词时，直接不做任何点击；
      2. 每个候选关闭按钮会检查其所在弹窗容器文本，命中上述关键词则跳过；
      3. 只点常见关闭按钮（aria-label=关闭、弹窗内的"关闭/知道了/继续/取消/跳过/不再提示"、
         class 含 close 的按钮/图标），并限制单次最多关闭 5 个，防止误点连串。
    """
    code = """
    async (page) => {
        const run = await page.evaluate(() => {
            const body = document.body;
            if (!body) return 'no-body';
            const SKIP_RE = /验证码|人机验证|安全验证|额度|用尽|拥挤|排队|优先队列|繁忙|限流|CAPTCHA|verifycenter|captcha|rmc\\.bytedance|滑动|拖动|点选|滑块/;
            const CLOSE_TEXTS = ['关闭', '知道了', '我知道了', '继续', '取消', '跳过', '暂不', '不再提示', 'X', '×'];
            // 页面整体若处于验证码/受限状态，直接不动作
            const head = (body.innerText || '').slice(0, 600);
            if (SKIP_RE.test(head)) return 'skip-page';
            let closed = 0;
            const safeClick = (el) => {
                if (!el || el.offsetParent === null) return false;
                const r = el.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) return false;
                const container = el.closest('.modal, [class*="modal"], [class*="dialog"], [role="dialog"], [class*="popup"], [class*="toast"]');
                const ctx = (container || el).innerText || '';
                const aria = el.getAttribute('aria-label') || '';
                if (SKIP_RE.test(ctx) || SKIP_RE.test(aria)) return false;
                try {
                    el.click();
                    closed += 1;
                    return true;
                } catch (e) {
                    return false;
                }
            };
            // 1) 明确的关闭按钮/图标
            document.querySelectorAll('[aria-label="关闭"], [aria-label="Close"], .modal-close, .close-btn, [class*="dialog-close"], [class*="popup-close"]').forEach((el) => {
                if (closed >= 5) return;
                safeClick(el);
            });
            // 2) 弹窗容器内的文字按钮
            document.querySelectorAll('.modal [class*="btn"], [class*="modal"] [class*="btn"], [role="dialog"] button, [class*="popup"] button, .modal button, [class*="modal"] button').forEach((el) => {
                if (closed >= 5) return;
                const txt = (el.innerText || '').trim();
                for (const c of CLOSE_TEXTS) {
                    if (txt === c || txt.indexOf(c) === 0) { safeClick(el); break; }
                }
            });
            return closed > 0 ? 'closed:' + closed : 'nothing';
        });
        return run;
    }
    """
    res = await client.call_tool("browser_run_code_unsafe", {"code": code})
    return PlaywrightMCP.parse_result(res)
