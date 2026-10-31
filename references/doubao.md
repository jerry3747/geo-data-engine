# 豆包网页版爬虫细节（改代码/排查时读）

## 页面与入口
- 首页 `https://www.doubao.com/chat/` 即是新会话：主面板显示"有什么我能帮你的吗？"。
- 没有独立的 `/search` 结果页。搜索结果 = 聊天回答，走"新会话发问题"。
- 发送后 URL 变为 `https://www.doubao.com/chat/<会话id>`，部分新会话会带 `local_` 前缀（如 `/chat/local_<id>`）——**这是正常的本地新会话，不是验证码/异常状态**（2026-09 实测确认）。

## 输入与发送
- 输入框：`div[contenteditable="true"].tiptap`（ProseMirror 编辑器）。
- 用 `locator().click({force: true})` + `pressSequentially(text, {delay: 20})` 真实键盘输入，再 `page.keyboard.press('Enter')` 发送。
- force 点击跳过"元素不稳定"等待，长跑后页面状态异常时不会一直卡在 click 超时。
- 发送后轮询：出现 `[data-message-id]` **或** URL 变成 `/chat/<id>` 即算发送成功；失败则**硬刷新 `goto('/chat/')` 重试**（最多 4 次），刷新能重置 SPA 状态。
- 关键词含引号/换行时，JS 字符串字面量用 `json.dumps(keyword, ensure_ascii=False)` 生成。

## 人机验证（验证码）
- **验证码是发送消息后才弹出的**（点选/拖动类），长跑/高频触发后出现；主文档里**没有验证码文字**，文字都在跨域 iframe 里，不能用 body 文字检测。
- **可靠信号（两路，均要求"可见"）**：
  1. 页面出现**可见的** `#captcha_container`（offsetWidth/Height > 0，容器平时可能是隐藏的）；
  2. 出现**可见的** `rmc.bytedance.com/verifycenter/captcha` iframe（字节验证码服务，size>0）。
- 检测到即返回明确错误（"检测到人机验证（container/iframe），请手动完成验证后重试"），不再干等/反复重试；批量模式遇到验证码跳过自动重试。
- **不要用 `local_` URL 前缀判验证码**：新会话正常 URL 就是 `/chat/local_<id>`，否则每条都会被误判成验证码。
- **不要用整页 body 文字匹配验证码关键词**：正常页面常驻"发消息或按住空格说话""探索灵感 滑动探索"等文案（含"按住""滑动"），回答正文也可能出现"验证码/重试"等词，会误判成验证码。错误文案（生成失败/请重试等）只在**没有回答文本**时才视为致命。
- 注意：发送成功判定要**排除 local_ 会话**（不能把 local_ URL 当发送成功）。
- 技能不自动破解验证码，需用户在浏览器里手动通过后重跑。

## 等待回答完成
- 每条消息容器：`[data-message-id="..."]`（虚拟列表 `[class*="message-list"]` 内）。
- 用户消息的 `[data-message-id]` 容器 class 含 `justify-end`；助手消息不含。
- 消息 markdown 根节点带 `data-streaming`：生成中为 `true`，结束后为 `false`；**思考中可能还没有该节点（ds=null）**。
- 结束判定（统一稳定路径，不用单轮判断）：最后一条是助手消息，其 `data-streaming == "false"`、文本非空，且**文本长度连续两轮不变**。ds 为 null 或 true 一律不算完成，避免"思考中/刚出空占位"被误判提前退出。
- 错误判定：页面出现"生成失败/请求失败/网络异常/出错了/请重试"；检测到验证码按上节处理。

## 抽取
- 消息列表：`document.querySelector('[class*="message-list"]')`。
- 每条消息：遍历 `[data-message-id]`，在其所有 `[data-streaming]` 节点中取**文本最长**的作为消息内容；若内容为空则回退用整个 `[data-message-id]` 的 innerText（防止占位节点为空时抽到空答案）。
- 引用链接：助手消息容器内 `a[href]` 且不含 `doubao.com`，去重。

## 踩坑
- `browser_run_code_unsafe` 里**没有全局 setTimeout**，等待用 `page.waitForTimeout(ms)`；`location`/`document` 只能在 `page.evaluate` 里用。
- MCP 返回值为 JSON 序列化，一律走 `parse_json_result`。
- JS 里注释必须用 `//`，别写 Python 的 `#`；return 对象属性之间要有逗号。
- class 多为 Tailwind + CSS Module 哈希，不要硬编码完整 class，用前缀/属性匹配。
- 新会话会在用户账号里新增聊天记录，这是预期行为。

