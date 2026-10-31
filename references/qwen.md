# 千问网页版爬虫细节（改代码/排查时读）

## 页面与入口
- `https://www.tongyi.com/` 会 302 到 `https://www.qianwen.com/?ch=tongyi_redirect`，直接用 `https://www.qianwen.com/` 即新会话（"你好，我是千问 / 向千问提问"）。
- 国际版 `chat.qwen.ai`（Qwen Studio）需要单独登录，本技能默认用已登录的 qianwen.com。
- 发送后 URL 变为 `https://www.qianwen.com/chat/<uuid>`。

## 输入与发送（重点：发送按钮必须"人手式"点击）
- 输入框：`div[contenteditable="true"][role="textbox"]`（Tailwind 类不稳定，用属性；取 `.nth(0)`，页面可能有离屏镜像）。
- **风控**：用回车或 `locator.click` 点发送，千问会识别为机器行为，回答一律变成"你好，我无法回答这个问题，我们换一个话题聊聊吧"。手动打字/粘贴 + 手动点击都正常。
- **正确发送方式**：输入前清空草稿（`Control+A`+`Delete`）→ 逐字输入（delay ~40ms）→ 校验 innerText 与关键词一致 → 取 `[aria-label="发送消息"]` 按钮中心，**分步移动鼠标（多次 move + 随机停顿）再 `page.mouse.click`** → 轮询确认出现 `.chat-round`。
- 兜底：找不到发送按钮时才用 Enter。
- 若回答出现"我无法回答这个问题"，wait_answer 直接报"千问风控拦截"，不把拒绝话术当答案保存。

## 等待回答完成
- 一轮问答容器：`.chat-round`（`last-message-item` 标记最后一条）。
- 千问对问题常自动联网搜索（"已完成分析，共参考 N 篇资料 / 搜索 N 个关键词"）。
- 完成判定：最后一段 `.qk-markdown` 的 innerText 长度**连续两轮不变**，且页面无"正在生成/思考/分析"状态；无回答连续 4 轮提前报错。

## 抽取
- 用户消息：`.chat-round .message-card-wrap.question` innerText；兜底用关键词。
- 助手回答：`.chat-round .qk-markdown`（完成态带 `qk-markdown-complete`；前面的"搜索 1 个关键词，参考 6 篇资料"等头尾信息不在里面，天然干净）。
- 引用来源：`.chat-round` 内所有外部 `a[href]`（排除 qianwen.com / tongyi.com），按 href 去重、取最长非数字标题。
- 千问网页不展示独立"思考"过程，`thinking` 字段留空。

## 踩坑
- `browser_run_code_unsafe` 外层是 Node 环境，DOM 必须包在 `page.evaluate(() => {...})` 里。
- MCP 返回值为 JSON 序列化，统一走 `parse_json_result`。
- 输入框类名是动态 Tailwind，别硬编码；`qk-markdown` 等业务类名相对稳定。
- 回答可能较长（上千字 + 表格），wait_rounds 默认 60。

