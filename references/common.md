# 通用注意事项（多平台 AI 搜索共用）

## 1. 所有鼠标/键盘操作必须真实手势
点击、输入、回车一律走 `browser_run_code_unsafe` + `locator().click()` / `pressSequentially()` / `page.keyboard.press()`。
禁止在 `browser_evaluate` 里用 `element.click()` / 直接设 innerText，多数平台监听真实事件，JS 合成事件不生效。

## 2. MCP 返回值带 JSON 引号（大坑）
Playwright MCP 会把返回值 JSON 序列化——普通字符串变 `"true"`（带引号），对象被转义。
`base.parse_result` 统一 `json.loads` 还原，判断状态前务必先还原。

## 3. `browser_run_code_unsafe` 里没有全局 setTimeout
等待一律 `page.waitForTimeout(ms)`，直接 `setTimeout` 会 `ReferenceError`。

## 4. 不要硬编码哈希 class
各平台前端多用 Tailwind + CSS Module，class 后缀每次发版会变（如 `message-list-zLoNs1`）。
用稳定的属性/前缀选择器：`[data-message-id]`、`[data-streaming]`、`[class*="message-list"]`、`[contenteditable]`、`[role="textbox"]`。

## 5. 等待回答完成
统一模式：轮询页面状态（如 `data-streaming` / "停止生成"字样 / 文本长度稳定）判定结束；
必须区分"还在生成"和"已完成"，避免截到半截答案。

## 6. 限流与风控
平台真实账号，别把 delay 调太小、别并发刷屏。单个问题一次会话即可，高频需求加大间隔。

## 7. 结果保存
统一存 `data/<日期>/<platform>_<关键词>_<时间>.json`（结构化）+ `.md`（可读）。
JSON 至少含 `platform / keyword / queried_at / url / answer / references / status / error`。

## 8. 无害噪音
脚本结束断开连接时会打印 anyio 异步生成器关闭报错，是无害噪音，不影响结果。

## 9. 平台
代码跨平台（路径全用 `os.path.join`，无硬编码）。运行命令：Windows 用 `python`，macOS 用 `python3`。
