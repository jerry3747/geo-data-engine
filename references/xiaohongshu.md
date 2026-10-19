# 小红书网页版 AI 聊天爬虫细节（改代码/排查时读）

## 页面与入口
- 入口：`https://www.xiaohongshu.com/ai_chat`（必须已登录小红书）。
- 打开即欢迎页（新会话）：问候语 + 推荐话题 + 输入框。
- 发送后 URL 变为 `https://www.xiaohongshu.com/ai_chat?conversationId=<uuid>`；标题形如"新会话-对话"。
- 同一 tab 内给下一个关键词发新问题时，重新 `goto('https://www.xiaohongshu.com/ai_chat')` 回欢迎页即可（新会话）。

## 输入与发送（重点坑）
- 输入框：`textarea.textarea--v1-caret`，位于 `.ai-chat-welcome__input`（欢迎页）或 `.input-container`（会话页）。
- **页面有两个完全重叠的 textarea**（同一位置叠放，占位文案还会轮换），直接 `locator.click()` 会点到底下那个导致输入无效。
  **必须用 `document.elementFromPoint(中心点)` 命中坐标上真正在顶层的 textarea**，再用 `page.mouse.click(x, y)` 点它。
- 输入用 `page.keyboard.type(...)`（真实按键）；发送用 **Enter** 或点右下角 `div.bottom-box-right-submit-button`。
  左侧的 `button.ai-input-action-btn` 是附件/工具按钮，不是发送键，别点。
- 发送成功判定：出现 `.user-message__text` 或 URL 含 `conversationId=`。

## 等待回答完成
- 助手消息容器：`.ai-message`（可能有多个，取最后一个）。
- **完成信号：`.ai-message-finished` class 出现**即回答结束（生成中无该 class）。
- 回答正文开头常带"ai总结N篇笔记生成"进度标题，抽取时用正则 `^ai\s*总结\s*\d+\s*篇笔记生成\s*\n*` 去掉。
- 回答可能很长（上千字、带表格/列表卡片），wait_rounds 默认给足。

## 抽取
- 用户消息：`.user-message__text` innerText。
- 助手回答：最后一个 `.ai-message` innerText（去掉进度标题）。
- 引用：助手消息内 `a[href]` 且不含 `xiaohongshu.com`，去重；推荐卡片 `.xhs-complex-list__item` 无链接时只算正文文本。
- 回答底部"内容由AI生成，请注意核实"等文案保留原文。

## 笔记链接解析（dqa/detail 接口）
- 回答完成后，**先注册** `page.waitForResponse` 监听 `https://edith.xiaohongshu.com/api/sns/web/v1/search/dqa/detail`，
  **再点击** `span.progress-text`（兜底 `.progress-content` / `[class*="progress"]`）触发接口。
- 接口返回 `data.items[]`，取每项的 `id` 与 `xsec_token`，拼接笔记链接：
  `https://www.xiaohongshu.com/explore/{id}?xsec_token={xsec_token}&xsec_source=pc_search`
- 笔记链接写入同目录 CSV（UTF-8 BOM：关键词/笔记ID/xsec_token/笔记标题/笔记链接），
  并合并进 `references`（title + href），分布式模式下随结果落库、导出 CSV 时一并带出。
- 接口没返回或解析失败时不影响主流程（回答照常保存），只是没有笔记链接。

## 踩坑
- `browser_run_code_unsafe` 外层无 `document`，DOM 必须包在 `page.evaluate` 里。
- MCP 返回值为 JSON 序列化，统一走 `parse_json_result`。
- 未登录时会跳登录墙；验证码/风控目前未遇到，若出现按错误文本处理。
