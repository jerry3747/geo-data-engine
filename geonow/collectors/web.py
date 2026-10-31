from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PROVIDER_DEFAULTS = {
    "doubao": {"url": "https://www.doubao.com/chat/", "name": "豆包"},
    "kimi": {"url": "https://kimi.moonshot.cn/", "name": "Kimi"},
}


@dataclass
class CollectionResult:
    status: str
    valid: bool
    answer_text: str = ""
    raw_answer: str = ""
    citations: list[dict[str, str]] = field(default_factory=list)
    screenshot_path: str | None = None
    error: str | None = None
    mode_settings: dict[str, Any] = field(default_factory=dict)


class WebCollector:
    """Playwright 网页采集器。每次 collect 都创建新的 browser context。"""

    def __init__(self, provider: str, output_dir: str | Path = "evidence", headless: bool = True,
                 timeout_ms: int = 45000, login_wait: bool = False, provider_config: dict[str, Any] | None = None):
        if provider not in PROVIDER_DEFAULTS:
            raise ValueError(f"暂不支持网页提供方: {provider}")
        self.provider = provider
        self.output_dir = Path(output_dir)
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.login_wait = login_wait
        self.config = {**PROVIDER_DEFAULTS[provider], **(provider_config or {})}

    def collect(self, question: str, batch_id: str, sample_no: int) -> CollectionResult:
        mode = {"provider": self.provider, "url": self.config["url"], "headless": self.headless,
                "new_context_per_sample": True, "timeout_ms": self.timeout_ms}
        screenshot = self.output_dir / batch_id / self.provider / f"sample-{sample_no:04d}.png"
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        try:
            from playwright.async_api import async_playwright  # type: ignore
        except ImportError:
            return CollectionResult("dependency_missing", False, screenshot_path=str(screenshot),
                                    error="未安装 Playwright。请运行 pip install -e '.[web]' && playwright install chromium",
                                    mode_settings=mode)
        import asyncio
        try:
            return asyncio.run(self._collect_async(question, screenshot, mode, async_playwright))
        except Exception as exc:  # 保留失败样本，不让一题中断整个批次
            return CollectionResult("failed", False, screenshot_path=str(screenshot), error=f"{type(exc).__name__}: {exc}", mode_settings=mode)

    async def _collect_async(self, question: str, screenshot: Path, mode: dict[str, Any], async_playwright: Any) -> CollectionResult:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.headless)
            # 不复用 storage_state/cookies，确保每个样本是新会话。
            context = await browser.new_context(viewport={"width": 1440, "height": 1100})
            page = await context.new_page()
            try:
                await page.goto(self.config["url"], wait_until="domcontentloaded", timeout=self.timeout_ms)
                await page.wait_for_timeout(int(self.config.get("login_probe_ms", 1500)))
                if await self._looks_like_login(page):
                    if self.login_wait and not self.headless:
                        print(f"[{self.provider}] 需要登录，请在打开的浏览器窗口完成登录后按回车继续。")
                        try:
                            input()
                        except EOFError:
                            pass
                    else:
                        await page.screenshot(path=str(screenshot), full_page=True)
                        return CollectionResult("login_required", False, screenshot_path=str(screenshot),
                                                error="检测到登录页；请使用 --headed --login-wait 后在浏览器完成登录",
                                                mode_settings=mode)
                await self._dismiss_overlays(page)
                await self._submit(page, question)
                await page.wait_for_timeout(int(self.config.get("settle_ms", 2500)))
                text = await self._extract_answer(page, question)
                citations = await self._extract_links(page)
                await page.screenshot(path=str(screenshot), full_page=True)
                if not text.strip():
                    body_after = (await page.locator("body").inner_text()).lower()
                    if "登录" in body_after or "sign in" in body_after:
                        return CollectionResult("login_required", False, screenshot_path=str(screenshot),
                                                error="提交后页面提示登录；请使用 --headed --login-wait 完成登录",
                                                citations=citations, mode_settings=mode)
                    return CollectionResult("empty", False, screenshot_path=str(screenshot),
                                            error="页面未提取到回答文本", citations=citations, mode_settings=mode)
                return CollectionResult("success", True, answer_text=text, raw_answer=text,
                                        citations=citations, screenshot_path=str(screenshot), mode_settings=mode)
            finally:
                try:
                    if not screenshot.exists():
                        await page.screenshot(path=str(screenshot), full_page=True)
                except Exception:
                    pass
                await context.close()
                await browser.close()

    async def _looks_like_login(self, page: Any) -> bool:
        url = (page.url or "").lower()
        if any(x in url for x in ("login", "signin", "passport")):
            return True
        body = (await page.locator("body").inner_text(timeout=5000)).lower()
        has_input = bool(await page.locator("textarea").count() or await page.locator('[contenteditable="true"]').count())
        login_button = page.locator("button:has-text('登录'), a:has-text('登录')")
        has_visible_login = False
        for i in range(await login_button.count()):
            try:
                if await login_button.nth(i).is_visible():
                    has_visible_login = True
                    break
            except Exception:
                continue
        return any(x in body for x in ("登录后", "请登录", "手机号登录", "sign in")) and not has_input or has_visible_login

    async def _submit(self, page: Any, question: str) -> None:
        textareas = page.locator("textarea")
        editable = page.locator('[contenteditable="true"]')
        try:
            await textareas.first.wait_for(state="visible", timeout=10000)
        except Exception:
            try:
                await editable.first.wait_for(state="visible", timeout=10000)
            except Exception:
                pass
        if await textareas.count():
            box = textareas.last
            await box.fill(question)
        else:
            if not await editable.count():
                raise RuntimeError("找不到输入框，页面结构可能已变化")
            await editable.last.fill(question)
        for selector in ["button:has-text('发送')", "button:has-text('Send')", "button:has-text('提交')", "[aria-label*='发送']"]:
            button = page.locator(selector)
            if await button.count():
                await button.last.click()
                return
        await page.keyboard.press("Enter")

    async def _dismiss_overlays(self, page: Any) -> None:
        """关闭首页下载/活动弹窗，避免遮挡输入框；失败时继续采集并留存截图。"""
        try:
            await page.keyboard.press("Escape")
            for selector in ["[aria-label*='关闭']", "[aria-label*='Close']", "button:has-text('×')", "[class*='close']"]:
                loc = page.locator(selector)
                for i in range(await loc.count()):
                    try:
                        if await loc.nth(i).is_visible():
                            await loc.nth(i).click(timeout=1000)
                            return
                    except Exception:
                        continue
        except Exception:
            return

    async def _extract_answer(self, page: Any, question: str) -> str:
        selectors = ["[data-testid*='message']", "[class*='markdown']", "article", "main"]
        chunks: list[str] = []
        for selector in selectors:
            loc = page.locator(selector)
            count = await loc.count()
            if count:
                for i in range(max(0, count - 5), count):
                    text = (await loc.nth(i).inner_text()).strip()
                    if text and text not in chunks:
                        chunks.append(text)
                if chunks:
                    break
        if not chunks:
            chunks = [(await page.locator("body").inner_text()).strip()]
        text = "\n\n".join(chunks)
        # 页面壳、输入框回显或登录提示不能算有效回答。
        cleaned = text.replace(question, "")
        for shell in ("新对话", "新工作任务", "AI 生成可能有误 请核实", "下载电脑版", "登录", "发送",
                      "有什么我能帮你的吗？", "对话", "工作", "快速", "最近", "项目", "API 服务", "更多"):
            cleaned = cleaned.replace(shell, "")
        return cleaned.strip()

    async def _extract_links(self, page: Any) -> list[dict[str, str]]:
        out = []
        links = page.locator("a[href]")
        for i in range(await links.count()):
            a = links.nth(i)
            href = await a.get_attribute("href") or ""
            text = (await a.inner_text()).strip()
            if href.startswith(("http://", "https://")):
                out.append({"url": href, "text": re.sub(r"\s+", " ", text)[:300]})
        return out


class FixtureCollector:
    """离线验证采集管线用的采集器，不冒充真实网页结果。"""

    def __init__(self, answer: str, output_dir: str | Path = "evidence", provider: str = "fixture"):
        self.answer = answer
        self.output_dir = Path(output_dir)
        self.provider = provider

    def collect(self, question: str, batch_id: str, sample_no: int) -> CollectionResult:
        import base64
        screenshot = self.output_dir / batch_id / self.provider / f"sample-{sample_no:04d}.png"
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        screenshot.write_bytes(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="))
        return CollectionResult("fixture_success", True, answer_text=self.answer, raw_answer=self.answer,
                                screenshot_path=str(screenshot), mode_settings={"source": "fixture", "provider": self.provider, "question": question})
