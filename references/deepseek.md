# DeepSeek 网页版爬虫细节（改代码/排查时读）

## 页面与入口
- 首页 `https://chat.deepseek.com/` 即新会话（"开始聊天吧"状态）。
- 发送后 URL 变为 `https://chat.deepseek.com/a/chat/s/<uuid>`，标题变为"<问题首句> - DeepSeek"。
- DeepSeek 对部分问题会自动联网搜索（"正在搜索 / 搜索到 N 个网页 / 浏览 N 个页面"），无需手动开"智能搜索"开关；按用户要求直接发问题即可。

## 输入与发送
- 输入框：`textarea[placeholder*="发送消息"]`（页面唯一可见 textarea）。
- 用 `locator().click()` + `pressSequentially(text, {delay: 20})` 真实键盘输入，`page.keyboard.press('Enter')` 发送（Enter=发送，Shift+Enter=换行）。
- 发送后轮询确认出现 `div.ds-message` 再继续；点击/输入/发送整套重试最多 3 次（页面加载期点击会偶发"元素不稳定"超时）。
- 关键词含引号时用 `json.dumps(keyword, ensure_ascii=False)` 生成 JS 字符串字面量。

## 等待回答完成
- 消息区是虚拟列表：`div.ds-virtual-list-visible-items`，每行一个消息。
- 生成状态：正文出现"正在思考 / 正在搜索 / 正在回答 / 思考中"。
- 完成判定：最后一段 `div.ds-markdown` 的 innerText 长度**连续两轮不变**，且页面无"正在…"状态；再叠加"页面无回答连续 4 轮提前报错"和错误文案检测（服务器繁忙/网络错误/出错了/请重试/登录已过期）。
- 注意：思考过程也是 `div.ds-markdown`，只看最后一段即可；回答是流式增长的，不能只靠"出现 markdown"判定完成。

## 抽取
- 助手回答正文：`div.ds-markdown.ds-assistant-message-main-content`（稳定类名，消息直接子节点之一）。
- 思考过程：`div.ds-think-content`（完成态有"已思考（用时 X 秒）"头部）。
- 用户消息：虚拟列表里不包含 answer 容器的行，`row.innerText`；若被虚拟化裁剪，用关键词兜底。
- 引用来源：助手消息行内所有外部 `a[href]`（排除 deepseek.com），按 href 去重，标题取最长非纯数字文本（内联引用"-1/-2"是数字标记，浏览面板的链接才是真标题）。

## 踩坑
- `browser_run_code_unsafe` 外层是 Node 环境，DOM 必须包在 `page.evaluate(() => {...})` 里。
- MCP 返回值为 JSON 序列化，统一走 `parse_json_result`（parse_result 后再 json.loads 一次）。
- class 大量哈希化（`_63c77b1`、`_4f9bf79` 等），只用稳定类 `ds-message`、`ds-markdown`、`ds-assistant-message-main-content`、`ds-think-content`、`ds-virtual-list-visible-items`。
- 回答可能较长（上千字 + 表格），wait_rounds 默认 60（约 3 分钟），联网搜索场景耗时更长。
- 文本里内联引用渲染为 "- / - / N" 换行格式，是页面渲染结果，保存时保留原样。
