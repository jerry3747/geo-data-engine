# 腾讯元宝网页版爬虫细节（改代码/排查时读）

## 页面与入口
- 首页 `https://yuanbao.tencent.com/` 会跳 `https://yuanbao.tencent.com/chat/<id>`（新会话），标题"元宝-腾讯旗下全能AI助手"。
- 发送后 URL 变为 `.../chat/<id>/<会话>`，标题变为问题摘要（如"AI搜索与传统搜索对比"）。
- 首页有"提问智能联网，无需手动切换""元宝支持语音输入啦"等"我知道了"弹窗，可能盖住输入框，必须先关。

## 输入与发送
- 输入框：`div.ql-editor`（Quill 编辑器，稳定类）。
- 发送前处理弹窗：循环按 Escape + 点击"我知道了"（最多 6 轮，弹窗可能连续出现）。
- 用 `locator().click({force: true})`（弹窗/动画会让普通 click 报"元素不稳定"）+ `pressSequentially` + Enter。
- 发送后轮询确认正文出现关键词再继续；点击/输入/回车整套重试最多 4 次。

## 防卡机制（长时间任务）
- 等待循环不是死循环（有总超时上限），但生成卡死时"停止回答"按钮会一直存在，原来会干等满超时（默认 180 秒且无输出）。
- 新增停滞检测：
  - **思考超时**：尚未出现回答（count=0）但停止按钮一直在，连续 30 轮（约 90 秒）无结果 → 报"长时间思考/检索无响应"提前退出。
  - **回答停滞**：回答已开始（count>0）但长度连续 8 轮（约 24 秒）无进展 → 报"回答停滞"提前退出。
- Python 侧给等待调用加外层 `asyncio.wait_for` 兜底（总超时 + 120 秒），即使浏览器 evaluate 挂起也不会永久阻塞。
- 触发停滞/超时都会返回明确错误并走批量层的重试逻辑。
## 等待回答完成
- 消息列表：`.agent-chat__list__content`，消息项 `.agent-chat__list__item__content`。
- 回答容器：`.hyc-content-md`，完成态追加类 `hyc-content-md-done`；生成中消息头显示"处理中"，完成后"已处理"。
- 完成判定：**停止按钮消失**（`div[aria-label="停止回答"]` 不存在）且最后一段 `.hyc-content-md` 带 `hyc-content-md-done`、长度**连续两轮不变**；"处理中/生成中"文字只在思考阶段出现，流式阶段不可靠，不能作为主要信号。
- 无回答提前报错只在**停止按钮也消失**时生效（还在思考/生成就不算失败，继续等），避免长回答思考超时被误判。

## 抽取
- 用户消息：`.agent-chat__list__item__content .hyc-content-text` innerText；兜底用关键词。
- 助手回答：`.agent-chat__list__item__content` 内**最后一个** `.hyc-content-md` 的 innerText（元宝会把思考过程也渲染成 `.hyc-content-md` 且排在回答前面，必须取最后一块，否则会抽到思考文本）。
- 引用来源：助手消息项内所有外部 `a[href]`（排除 yuanbao.tencent.com），按 href 去重、取最长非数字标题；元宝联网回答的引用以回答内链接呈现。
- 元宝网页不展示独立"思考"过程，`thinking` 字段留空。

## 踩坑
- `browser_run_code_unsafe` 外层是 Node 环境，DOM 必须包在 `page.evaluate(() => {...})` 里。
- MCP 返回值为 JSON 序列化，统一走 `parse_json_result`。
- 弹窗必须处理，否则点击输入框会一直"元素不稳定"超时。
- 回答可能较长（上千字 + 表格），wait_rounds 默认 60。



