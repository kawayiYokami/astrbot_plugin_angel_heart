# AngelHeart 上下文注入使用指南

AngelHeart 把当前对话记录和秘书决策挂到本事件的 `event.angelheart_context`（JSON 字符串），供日志和下游钩子读取。秘书决策只决定回不回，不进主脑请求体。

## 数据结构

注入内容只包含两个字段：

### chat_records

当前对话历史。每条记录常见字段：

```json
{
  "role": "user",
  "content": [
    {"type": "text", "text": "@小助手 这个Python代码怎么调试？"}
  ],
  "sender_id": "123456",
  "sender_name": "小明",
  "timestamp": 1640995200.123
}
```

`content` 可能是多模态列表，也可能是纯字符串。下游应同时兼容两种形态。

### secretary_decision

秘书决策字段以 `SecretaryDecision` 为准：

```json
{
  "should_reply": true,
  "is_questioned": false,
  "is_interesting": true,
  "reply_strategy": "提供技术解决方案",
  "topic": "Python代码调试",
  "reply_target": "小明",
  "entities": ["小明", "Python调试"],
  "facts": ["小明询问调试方法"],
  "keywords": ["Python", "调试"]
}
```

- `should_reply`：是否唤醒主脑
- `is_questioned`：是否被追问
- `is_interesting`：话题是否适合介入
- `reply_strategy`：计划采用的策略；不回复时为「继续观察」
- `topic`：当前唯一核心话题
- `reply_target`：回复目标；不回复时为空字符串
- `entities` / `facts` / `keywords`：检索线索，不代表系统一定要搜索

过时字段 `needs_search` 不再注入。下游不要假设存在未声明的额外字段。

## 完整示例

```json
{
  "chat_records": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "@小助手 这个Python代码怎么调试？"}
      ],
      "sender_id": "123456",
      "sender_name": "小明",
      "timestamp": 1640995200.123
    }
  ],
  "secretary_decision": {
    "should_reply": true,
    "is_questioned": false,
    "is_interesting": true,
    "reply_strategy": "提供技术解决方案",
    "topic": "Python代码调试",
    "reply_target": "小明",
    "entities": ["小明", "Python调试"],
    "facts": ["小明询问调试方法"],
    "keywords": ["Python", "调试"]
  }
}
```

## 在事件处理器中读取

```python
import json
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest

class MyPlugin:
    @filter.on_llm_request()
    async def access_angelheart_context(self, event: AstrMessageEvent, request: ProviderRequest):
        if not getattr(event, "angelheart_context", None):
            return

        try:
            context = json.loads(event.angelheart_context)
        except (json.JSONDecodeError, TypeError):
            return

        chat_records = context.get("chat_records", [])
        secretary_decision = context.get("secretary_decision", {})

        if secretary_decision.get("should_reply"):
            strategy = secretary_decision.get("reply_strategy", "")
            if strategy:
                extra = f"本轮建议回复策略：{strategy}。"
                request.system_prompt = (
                    f"{request.system_prompt}\n{extra}" if request.system_prompt else extra
                )
```

## 注意事项

- 仅在 AngelHeart 活跃且本事件走过秘书路径时才会注入
- JSON 解析失败会记日志，不中断事件流
- 数据只在当前事件生命周期内有效
- 多个插件可以同时读取同一份上下文
- 建议在 `@filter.on_llm_request()` 读取；这是 LLM 调用前的旁路观察点，不是改写主脑历史的入口
