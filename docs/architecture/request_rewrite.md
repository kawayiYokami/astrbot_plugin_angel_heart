# 主脑请求重写

AngelHeart 在 `@filter.on_llm_request` 拦截已预填充的 `ProviderRequest`，用账本历史覆盖上游上下文。秘书决策不进主脑请求体。

开关：

- 群聊：`group_chat_enhancement`
- 私聊：`takeover_private_chat_context`
- 白名单开启时，非白名单会话不接管

入口是 `FrontDesk.rewrite_prompt_for_llm`。

## 历史从哪来

- **群聊**：必须用秘书判断点固化的切片，秘书看到的上下文和主脑看到的上下文同一边界。
- **私聊**：无秘书，从 `ConversationLedger` 读取；连续块过短时可冷启动补种。入场整理与补种互斥。
- 当前事件对应的消息从 `contexts` 中拆出，避免与 `req.prompt` 重复。

`MessageProcessor` 负责把账本内部消息转成上游 context：工具调用保持原结构，文本加结构化标签，图片按 Provider 能力决定是否保留。

## 临时注入

`req.contexts` 末尾只追加不保存的提醒：

1. 工作账本（第二人称，避免重复回答）
2. 群聊回复字数提醒（常规 / 焦点指令）

秘书决策、检索字段、`needs_search` 都不进主脑请求。

## 当前轮图片

按 Provider `modalities` 处理：

- 支持 `image`，或未声明 / 空列表：当前轮图片走 AstrBot 原生 `req.image_urls`。AngelHeart 会把附件路径换成账本缓存路径，避免引用已失效的临时文件。
- 不支持 `image`：清空 `req.image_urls`，并过滤上下文中的图片块。主模型需要某张图细节时，再调用 `angel_describe_image`。
- 防抖窗口内的非当前图片，通过 `extra_user_content_parts` 追加为当前请求的额外多模态块；窗口外的老图不搬过来。
- 本地文件已失效的图片块会从上下文剔除，避免把无效路径传给只认 base64 / http 的上游。

`image_caption_provider_id` 只供按需图片工具使用，不会自动为每张图生成描述。

## 仅 @ 无正文

秘书判定回复后放行主脑时，如果 `message_str` 为空且没有媒体 / 引用组件，AstrBot 核心会直接跳过 Agent。AngelHeart 已登记 `running` 工作，若不补正文，`assistant_busy` 会卡住同会话，直到 `running_timeout` 兜底。

放行前把 At 组件的显示名补回 `event.message_str`（无显示名时用 `@QQ号`），只改 `message_str`，原始组件链与账本内容不变。

## 模块关系

```text
main.delegate_prompt_rewriting
→ FrontDesk.rewrite_prompt_for_llm
   → 秘书判断点切片 / 私聊账本
   → MessageProcessor 转换
   → 工作账本 + 回复长度提醒
   → 按 Provider 能力过滤图片
   → 覆盖 req.contexts / req.prompt / req.image_urls
```
