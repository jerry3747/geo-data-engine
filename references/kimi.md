# Kimi 网页版爬虫细节（改代码/排查时读）

## 页面与入口
- 首页 `https://www.kimi.com/`（`kimi.moonshot.cn` 会重定向过来）即新会话。
- 发送后 URL 变为 `https://www.kimi.com/chat/<uuid>?chat_enter_method=home`，标题变为"<问题首句> - Kimi"。
- 按用户要求直接发问题，不手动开"联网搜索"开关；Kimi 对需要时效性的问题会自动联网，引用以回答内脚注/链接呈现。

## 输入与发送（重点）
- 输入框：`div.chat-input-editor`（Lexical 编辑器，contenteditable，稳定类名）。
- **必须先清空草稿再输入**：Kimi 会保存上次的草稿并在页面加载后恢复，直接 `pressSequentially` 会把新文字拼在旧草稿后面（出现"关键词×N"），导致发送失效。发送前 `Control+A`+`Delete` 清空并校验 innerText 为空（最多 3 次，兜底用 evaluate 置空）。
- 发送动作：**点击发送按钮** `.send-button-container`（带 Send 图标的 div，force 点击）；点不到才用 Enter 兜底。
- **拥挤弹窗（致命点）**：弹窗本身能停留很久，**绝不能因为程序输入/点击把它点掉**。任何输入/发送动作前先查 `window.__kimiBusySeen`，有就直接返回 BUSY 且**不再对页面做任何操作**；发送后没消息也不在同页重输（交给上层开新页重试）。
- **捕获一闪而过**：MutationObserver 监听 DOM（`聊天的人太多/人太多了/优先队列/排队/服务器繁忙/拥挤`），弹窗文字只要出现就置标志位，轮询读标志位；聊天接口 `ChatService/Chat` 返回 HTTP >= 400 也按拥挤处理（流式响应体读不了，只看状态码）。
- **拥挤文案只扫聊天区/输入区之外（排除 `.chat-content-list` 和 `.chat-input*`）**：回答正文里出现"排队/拥挤/繁忙/优先队列"等词很常见，**输入框占位文案更是常驻"输入消息，按回车排队发送"（含"排队"）**，不能按整页 body 文字判弹窗，否则每条都会误判为受限；弹窗是页面级浮层，不在聊天列表和输入区内，按 UI 文案扫描即可命中。
- **额度耗尽弹窗（"额度已用尽"）**：点击发送后可能弹出 `modal-container / modal-mask` 弹窗（fixed、z-index 高），按 UI 区文案 + 直接扫 `.modal-container, .modal-mask, [role="dialog"]` 文本检测，命中返回 `QUOTA` 错误。kimi.run() 必须把 `QUOTA` 视为发送失败/受限（否则会误当成发送成功去抽取，报"no chat-content-item"）。QUOTA/BUSY 都归入"受限"，不重试、本批次跳过该平台。
- 发送后轮询（每 250ms）确认：
  - 出现 `.chat-content-item-assistant` → 发送成功；
  - 出现拥挤弹窗标记（`聊天的人太多/人太多了/优先队列/排队/服务器繁忙/拥挤`）→ 尝试点"继续/取消/知道了"关闭，持续 3 轮仍拥挤 → 返回 `BUSY` 明确报错，**不重试**（被限流，重试无意义）。

## 等待回答完成
- 消息列表：`.chat-content-list`，每条消息 `.chat-content-item`（`.chat-content-item-user` / `.chat-content-item-assistant`）。
- 生成状态：正文出现"正在思考中 / 正在生成 / 生成中 / 停止生成 / 停止输出"。
- 完成判定：最后一条 `.chat-content-item-assistant` 的 innerText 长度**连续两轮不变**，且无"正在…"状态；
  无助手消息连续 4 轮提前报错；错误文案检测（出错了/请求失败/网络错误/请重试/发生错误/服务异常）。

## 抽取
- 用户消息：`.chat-content-item-user .user-content` innerText；被裁剪时用关键词兜底。
- 助手消息：`.chat-content-item-assistant .segment-container` 内：
  - 回答正文：最后一个 `.markdown-container:not(.toolcall-content-text) .markdown`（思考块也带 `.toolcall-content-text`，要排除）。
  - 思考过程：`.toolcall-content-text .markdown` 文本 + 标题 `.toolcall-title-name-text`（如"AI搜索概念与核心技术解析"），思考默认折叠（高度 0）但 innerText 可取。
- 引用来源：助手消息行内所有外部 `a[href]`（排除 kimi.com），按 href 去重、取最长非数字标题（与 deepseek 一致）。

## 踩坑
- `.message-list` 是空壳容器，真实消息在 `.chat-content-list`，别用错。
- `browser_run_code_unsafe` 外层是 Node 环境，DOM 必须包在 `page.evaluate(() => {...})` 里。
- MCP 返回值为 JSON 序列化，统一走 `parse_json_result`（parse_result 后再 json.loads 一次）。
- 助手消息里可能夹带推广横幅（`upgrade-membership`，如"高峰时段算力不足…"），不属于回答，靠 `.markdown` 选择器自动排除。
- 回答可能较长（上千字 + 表格），wait_rounds 默认 60（约 3 分钟）。



