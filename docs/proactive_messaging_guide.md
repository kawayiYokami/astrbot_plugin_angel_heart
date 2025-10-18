# 主动发送消息指南

在 `AstrBot` 插件开发中，我们有两种主要的方式来发送消息：**响应式发送**和**主动式发送**。本指南将重点介绍“主动式发送”的方法及其适用场景。

## 1. 两种发送方式的区别

### 响应式发送 (Reactive Sending)

这是最常见的方式，通常用于指令处理器 (`@filter.command`) 或事件处理器中。它通过 `yield` 一个 `event` 的结果对象来**返回**一个消息给框架，由框架负责后续的发送。

**特点**：
- 依赖于一个正在处理的 `event` 对象。
- 使用 `yield event.plain_result("...")` 或 `yield event.chain_result([...])`。
- 适用于需要对某个事件做出直接回应的场景。

**示例**：
```python
from astrbot.api.event import AstrMessageEvent
import astrbot.api.message_components as Comp

@filter.command("helloworld")
async def helloworld(self, event: AstrMessageEvent):
    # 通过 yield 返回结果，由框架发送
    chain = [
        Comp.Plain("你好！"),
        Comp.Image.fromURL("https://example.com/image.jpg")
    ]
    yield event.chain_result(chain)
```

### 主动式发送 (Proactive Sending)

这种方式不依赖于 `yield` 返回值，而是通过调用 `Context` 对象上的方法，**直接命令**框架发送一条消息。

**特点**：
- 不直接依赖于 `event` 对象（但需要 `chat_id`）。
- 使用 `await self.context.send_message(...)`。
- 适用于需要在后台任务、定时任务、或任何脱离了原始事件上下文的场景中发送消息。

**示例**：
```python
from astrbot.api.event import AstrMessageEvent
from astrbot.core.message.components import Plain

@filter.command("test_proactive")
async def test_proactive_send(self, event: AstrMessageEvent):
    chat_id = event.unified_msg_origin
    message_to_send = [Plain("这是一条主动发送的消息！")]
    
    # 直接调用 context 上的方法发送
    await self.context.send_message(chat_id, message_to_send)
```

## 2. 如何实现主动发送消息

实现主动发送的核心在于获取 `Context` 对象和构建 `MessageChain`。

### 步骤 1: 获取 `Context` 对象

在任何继承自 `Star` 的主插件类中，`Context` 对象通常在 `__init__` 方法中被注入，并可以通过 `self.context` 访问。

```python
from astrbot.core.star.context import Context

class MyPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        # 现在 self.context 就是可用的 Context 对象
        self.context = context 
```

如果你需要在其他类（如 `AngelHeartContext`）中使用它，最好的方法是通过构造函数将其传递进去。

```python
# 在 MyPlugin 中
self.my_helper = MyHelperClass(self.context)

# 在 MyHelperClass 中
class MyHelperClass:
    def __init__(self, astr_context: Context):
        self.astr_context = astr_context
```

### 步骤 2: 构建消息体 (MessageChain)

`send_message` 方法的第二个参数需要一个消息链。你有两种灵活的方式来构建它：

#### 方式 A: 使用组件列表 (推荐)

这是最灵活、最清晰的方式。你需要从 `astrabot.core.message.components` 或 `astrabot.api.message_components` 导入所需的组件。

```python
import astrbot.core.message_components as Comp

# 构建一个复杂的消息链
chain = [
    Comp.At(qq="123456"),
    Comp.Plain("你好，这是一条包含多种内容的消息："),
    Comp.Image.fromURL("https://example.com/image.jpg"),
    Comp.Plain("希望你喜欢！")
]

await self.context.send_message(chat_id, chain)
```

#### 方式 B: 使用 `MessageChain` 构造器

这种方式使用链式调用，对于简单的纯文本消息比较方便。

```python
from astrbot.api.event import MessageChain

# 构建一个简单的文本消息
message_chain = MessageChain().message("Hello, World!")
await self.context.send_message(chat_id, message_chain)

# 构建一个复杂消息
message_chain = MessageChain().message("来看图：").file_image("path/to/image.jpg")
await self.context.send_message(chat_id, message_chain)
```

## 3. 总结

| 特性 | 响应式发送 (`yield`) | 主动式发送 (`await`) |
|---|---|---|
| **适用场景** | 指令/事件的直接回复 | 后台任务、定时任务、无`event`上下文的场景 |
| **核心** | `yield event.result()` | `await context.send_message()` |
| **依赖** | 必须持有 `event` 对象 | 必须持有 `context` 对象 |
| **灵活性** | 简单直接 | 更高，可以在任何`async`函数中调用 |

在开发复杂插件时（如 `AngelHeart`），经常需要在后台任务中与用户交互（例如发送“正在思考中”的提示），此时，掌握主动式消息发送就变得至关重要。
