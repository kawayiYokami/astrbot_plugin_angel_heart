# AngelHeart 主动应答 API 使用指南

## 概述

AngelHeart 主动应答管理器提供了灵活的接口，支持开发自定义主动应答功能。通过异步任务实现，支持立即触发、延迟触发和定时触发三种模式。

## 核心组件

### ProactiveManager

主动应答管理器，负责管理所有主动应答任务。

### ProactiveRequest

主动应答请求对象，包含触发所需的所有信息。

## 使用方法

### 1. 基本访问

```python
# 在插件中获取主动应答管理器
proactive_manager = angel_context.proactive_manager
```

### 2. 立即触发主动应答

```python
async def immediate_reply_example(chat_id: str):
    """立即触发主动应答示例"""
    success = await proactive_manager.trigger_immediate(
        chat_id=chat_id,
        strategy="主动问候",
        topic="日常问候",
        context_data={"time": "morning"}
    )
    if success:
        print("主动应答触发成功")
```

### 3. 延迟触发主动应答

```python
async def delayed_reply_example(chat_id: str):
    """延迟5秒触发主动应答"""
    success = await proactive_manager.trigger_delayed(
        chat_id=chat_id,
        strategy="温馨提示",
        topic="休息提醒",
        delay_seconds=5.0,
        context_data={"type": "rest"}
    )
```

### 4. 定时触发主动应答

```python
import time

async def scheduled_reply_example(chat_id: str):
    """在指定时间触发主动应答"""
    # 明天上午9点
    tomorrow_9am = time.time() + 24 * 3600 + 9 * 3600
    
    success = await proactive_manager.trigger_scheduled(
        chat_id=chat_id,
        strategy="定时问候",
        topic="早安问候",
        scheduled_time=tomorrow_9am
    )
```

### 5. 带回调的主动应答

```python
async def reply_callback(chat_id: str, decision, context_data: Dict):
    """主动应答完成回调"""
    print(f"主动应答完成: {decision.topic}")
    # 可以在这里执行后续处理

async def callback_example(chat_id: str):
    """带回调的主动应答示例"""
    await proactive_manager.trigger_immediate(
        chat_id=chat_id,
        strategy="智能回复",
        topic="技术讨论",
        callback=reply_callback
    )
```

## 自定义触发器

### 1. 注册自定义触发器

```python
async def weather_trigger(chat_id: str, context_data: Dict) -> bool:
    """天气相关触发器"""
    # 检查是否是天气相关的话题
    weather_keywords = ["天气", "气温", "下雨", "晴天"]
    
    # 获取最近消息（这里需要你自己实现）
    recent_messages = get_recent_messages(chat_id)
    
    # 检查是否包含天气关键词
    for msg in recent_messages:
        content = msg.get("content", "")
        if any(keyword in content for keyword in weather_keywords):
            # 触发主动应答
            return await proactive_manager.trigger_immediate(
                chat_id=chat_id,
                strategy="天气回复",
                topic="天气信息",
                context_data={"trigger": "weather"}
            )
    
    return False

# 注册触发器
proactive_manager.register_custom_trigger("weather", weather_trigger)
```

### 2. 调用自定义触发器

```python
async def check_triggers(chat_id: str):
    """检查所有触发器"""
    # 调用特定触发器
    result = await proactive_manager.call_custom_trigger(
        "weather",
        chat_id,
        {"location": "beijing"}
    )
    
    if result:
        print("天气触发器执行成功")
```

### 3. 注销自定义触发器

```python
proactive_manager.unregister_custom_trigger("weather")
```

## 任务管理

### 1. 查看活跃任务

```python
async def show_active_tasks():
    """显示所有活跃的主动应答任务"""
    tasks = proactive_manager.get_active_tasks()
    
    for chat_id, task_info in tasks.items():
        print(f"会话 {chat_id}:")
        print(f"  触发类型: {task_info['trigger_type']}")
        print(f"  策略: {task_info['strategy']}")
        print(f"  话题: {task_info['topic']}")
```

### 2. 取消任务

```python
async def cancel_task(chat_id: str):
    """取消指定会话的主动应答任务"""
    success = await proactive_manager.cancel_chat_task(chat_id)
    if success:
        print(f"已取消会话 {chat_id} 的主动应答任务")
```

## 实际应用场景

### 1. 定时提醒

```python
async def setup_daily_reminder(chat_id: str, reminder_time: str):
    """设置每日提醒"""
    # 解析时间字符串（如 "09:00"）
    hour, minute = map(int, reminder_time.split(":"))
    
    # 计算下次触发时间
    import datetime
    now = datetime.datetime.now()
    next_trigger = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    
    if next_trigger <= now:
        next_trigger += datetime.timedelta(days=1)
    
    timestamp = next_trigger.timestamp()
    
    await proactive_manager.trigger_scheduled(
        chat_id=chat_id,
        strategy="定时提醒",
        topic="每日提醒",
        scheduled_time=timestamp,
        context_data={"type": "daily", "time": reminder_time}
    )
```

### 2. 智能对话

```python
async def smart_dialogue_trigger(chat_id: str, context_data: Dict) -> bool:
    """智能对话触发器"""
    # 分析对话情绪
    emotion = analyze_conversation_emotion(chat_id)
    
    if emotion == "sad":
        # 检测到负面情绪，延迟3秒后安慰
        await proactive_manager.trigger_delayed(
            chat_id=chat_id,
            strategy="情感安慰",
            topic="情绪支持",
            delay_seconds=3.0,
            context_data={"emotion": emotion}
        )
        return True
    
    return False

# 注册智能对话触发器
proactive_manager.register_custom_trigger("smart_dialogue", smart_dialogue_trigger)
```

### 3. 事件响应

```python
async def event_response(chat_id: str, event_type: str):
    """事件响应"""
    strategies = {
        "new_member": "新人欢迎",
        "member_leave": "告别送行",
        "birthday": "生日祝福"
    }
    
    if event_type in strategies:
        await proactive_manager.trigger_immediate(
            chat_id=chat_id,
            strategy=strategies[event_type],
            topic=f"事件响应:{event_type}",
            context_data={"event": event_type}
        )
```

## 注意事项

1. **状态检查**: 主动应答只在 `NOT_PRESENT` 状态下触发
2. **任务唯一性**: 每个会话同时只能有一个活跃的主动应答任务
3. **异常处理**: 所有触发器都应该包含适当的异常处理
4. **资源清理**: 插件卸载时应该调用 `cleanup()` 清理所有任务

## 最佳实践

1. **合理使用延迟**: 避免过于频繁的主动应答
2. **上下文感知**: 根据对话上下文决定是否触发主动应答
3. **优雅降级**: 主动应答失败时不影响正常功能
4. **日志记录**: 记录主动应答的触发情况便于调试

通过这个 API，你可以轻松实现各种自定义的主动应答功能，让 AngelHeart 更加智能和人性化。