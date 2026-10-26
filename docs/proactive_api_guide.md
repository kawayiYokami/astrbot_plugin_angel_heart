# AngelHeart 主动应答 API 使用指南

`ProactiveManager` 提供立即、延迟、定时三种主动应答，以及自定义触发器注册。主动应答只在群聊离场（`NOT_PRESENT`）时触发；每个会话同时只能有一个活跃任务。

发送消息本身走 AstrBot 的 `Context.send_message`。本插件负责的是触发时机、状态检查和决策对象。

## 核心组件

- `ProactiveManager`：管理全部主动应答任务
- `ProactiveRequest`：一次触发请求
- `ProactiveTriggerType`：`immediate` / `delayed` / `scheduled`

访问入口：

```python
proactive_manager = angel_context.proactive_manager
```

## 触发方式

### 立即触发

```python
success = await proactive_manager.trigger_immediate(
    chat_id=chat_id,
    strategy="主动问候",
    topic="日常问候",
    context_data={"time": "morning"},
)
```

当前状态不是离场时直接返回 `False`。

### 延迟触发

```python
success = await proactive_manager.trigger_delayed(
    chat_id=chat_id,
    strategy="温馨提示",
    topic="休息提醒",
    delay_seconds=5.0,
    context_data={"type": "rest"},
)
```

安排新任务前会取消该会话已有任务。

### 定时触发

```python
import datetime

now = datetime.datetime.now()
tomorrow_9am = (now + datetime.timedelta(days=1)).replace(
    hour=9, minute=0, second=0, microsecond=0
).timestamp()

success = await proactive_manager.trigger_scheduled(
    chat_id=chat_id,
    strategy="定时问候",
    topic="早安问候",
    scheduled_time=tomorrow_9am,
)
```

`scheduled_time` 使用本地时间戳。

### 带回调

```python
async def reply_callback(chat_id: str, decision, context_data: dict):
    # decision 是 SecretaryDecision
    # 需要真正发消息时，在回调里调用 AstrBot Context.send_message
    pass

await proactive_manager.trigger_immediate(
    chat_id=chat_id,
    strategy="智能回复",
    topic="技术讨论",
    callback=reply_callback,
)
```

执行成功后会构造 `should_reply=True` 的 `SecretaryDecision`，再调用回调。主动应答路径会把会话状态切到兼容态 `SUMMONED`，这不是现行进场条件，下游不要按它新增业务分支。

## 自定义触发器

```python
async def weather_trigger(chat_id: str, context_data: dict) -> bool:
    return await proactive_manager.trigger_immediate(
        chat_id=chat_id,
        strategy="天气回复",
        topic="天气信息",
        context_data={"trigger": "weather"},
    )

proactive_manager.register_custom_trigger("weather", weather_trigger)
await proactive_manager.call_custom_trigger("weather", chat_id, {"location": "beijing"})
proactive_manager.unregister_custom_trigger("weather")
```

触发函数签名：`async func(chat_id: str, context_data: Dict) -> bool`。

## 任务管理

```python
tasks = proactive_manager.get_active_tasks()
await proactive_manager.cancel_chat_task(chat_id)
```

插件卸载时应调用 `cleanup()` 清理全部任务。

## 规则

1. 只在 `NOT_PRESENT` 触发；状态已变则取消
2. 每个会话同时只有一个活跃任务
3. 真正发消息由回调或调用方完成，管理器本身不发送
4. 主动应答失败不影响常规消息路径
