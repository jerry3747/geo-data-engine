# 秘塔 AI 搜索爬虫细节（改代码/排查时读）

## 页面与入口
- 首页 `https://metaso.cn/`（秘塔AI搜索），已登录用户显示"用户131****3660"。
- 回车搜索后 URL 变为 `https://metaso.cn/chat/<id>`，标题变为搜索词本身。
- 秘塔本身就是 AI 搜索产品，无独立聊天消息流，结果是"AI 回答 + 来源"页面。

## 输入与发送
- 输入框：`textarea.search-consult-textarea`（占位符提示 Enter 发送，Shift+Enter 换行；稳定前缀）。
- 回车发送；点击/输入/回车整套重试最多 4 次（首页有轮播动画，点击会偶发"元素不稳定"超时）。
- 发送成功判定：URL 含 `/chat/`，或出现结果标题 `.search-title_result-title__Qtgg4` / AI 回答标记。
- 注意：结果页输入框类名会变，发送后不要再操作原 textarea；发送函数末尾兜底检查 URL 是否已进结果页。

## 等待回答完成
- AI 回答容器：`.markdown-body` 且内部含 `[data-testid^="_MarkDownContent"]`（区别于来源摘要的 `_AbstractMarkdown`）。
- 完成判定：回答 markdown 长度**连续两轮不变**，且页面无"正在检索/思考/生成/加载"状态；无回答连续 4 轮提前报错。
- 完成态有"思考了 X.XXs"字样（生成中为"正在思考…"）。

## 抽取
- AI 回答：上面说的 markdown-body 的 innerText；若首行等于关键词（有时回答会把问题作为 H1）则剥掉。
- 引用/来源：
  - `references`：页面外部 `a[href]`（排除 metaso.cn），按 href 去重、取最长非数字标题（多数查询来源卡片是 JS 点击，不一定有真实链接）。
  - `origins`：右侧来源列表 `.search-origin-box` 的标题文本（去重），没链接时作为来源证据。
- 内联引用编号：回答里的 `reference-num` span（`data-number` / `data-link-index`），innerText 不包含数字，取文本干净。

## 踩坑
- `browser_run_code_unsafe` 外层是 Node 环境，`location`/`document` 只能在 `page.evaluate(() => {...})` 里用；在 evaluate 外用 `location` 会 ReferenceError（已踩）。
- MCP 返回值为 JSON 序列化，统一走 `parse_json_result`。
- `.markdown-body` 同时用于 AI 回答和来源摘要，必须靠 `data-testid` 前缀区分。
- 回答可能较长，wait_rounds 默认 60。
