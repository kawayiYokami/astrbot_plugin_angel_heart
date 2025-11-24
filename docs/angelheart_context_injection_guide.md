# AngelHeart 上下文注入使用指南

## 概述

AngelHeart 插件实现了一种智能的上下文注入机制，通过在 AstrBot 事件流中注入结构化的对话上下文，帮助其他插件更好地理解和响应用户交互。

AngelHeart 通过分析对话历史、决策是否回复以及搜索需求等信息，将这些上下文数据以 JSON 字符串的形式注入到 `AstrMessageEvent` 对象的 `angelheart_context` 属性中，供下游插件使用。

## 上下文数据结构

注入的上下文以 JSON 字符串形式存储在 `event.angelheart_context` 中，包含以下三个核心字段：

### 1. chat_records（聊天记录）

包含当前对话的完整历史记录，每个记录包含：

```json
{
  "role": "user|assistant",
  "content": "消息内容",
  "sender_id": "发送者ID",
  "sender_name": "发送者昵称",
  "timestamp": 1640995200.0,
  "is_processed": true
}
```

### 2. secretary_decision（秘书决策）

包含 AngelHeart 秘书AI的分析决策结果：

```json
{
  "should_reply": true,              // 是否需要介入
  "is_questioned": false,             // 是否被追问（用户在继续之前的话题或要求回应之前的回答）
  "is_interesting": false,            // 话题是否有趣（符合AI身份、能提供价值、介入合适）
  "reply_strategy": "技术指导",       // 概述你计划采用的策略。如果 should_reply 为 false，此项应为 '继续观察'
  "topic": "Python调试问题",          // 对当前唯一核心话题的简要概括
  "reply_target": "小明",            // 回复目标用户的昵称或ID。如果不需要回复，此项应为空字符串
  "entities": ["小明", "Python调试"], // 实体列表，优先级最高的发言人ID，其次是其他对话中的实体（包含但不限于人物、话题、物品、时间、地点、活动等）
  "facts": ["小明询问Python调试"],    // 极简日志模式。只保留'谁 做了 什么'或'谁 提议 什么'。单句禁止超过15个字，禁止形容词
  "keywords": ["Python", "调试"]      // 1-3个核心搜索词
}
```

### 3. needs_search（全局搜索标志）

一个布尔值，表示当前对话是否需要进行网络搜索来获取额外信息。

## 完整上下文示例

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
      "timestamp": 1640995200.123,
      "is_processed": true
    },
    {
      "role": "assistant",
      "content": [
        {"type": "text", "text": "你可以使用print语句或断点来调试代码"}
      ],
      "sender_id": "bot123",
      "sender_name": "小助手",
      "timestamp": 1640995260.456,
      "is_processed": true
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
  },
  "needs_search": false
}
```

## 在事件处理器中访问上下文

### 基本访问方法

在 `@filter.on_llm_request()` 或其他事件处理器中，可以通过以下方式访问注入的上下文：

```python
import json
from astrbot.api.event.filter import on_llm_request
from astrbot.api.provider import ProviderRequest

class MyPlugin(Star):
    @on_llm_request()
    async def access_angelheart_context(self, event: AstrMessageEvent, request: ProviderRequest):
        """访问 AngelHeart 注入的上下文信息"""

        # 检查是否存在 angelheart_context 属性
        if not hasattr(event, 'angelheart_context') or not event.angelheart_context:
            logger.info("AngelHeart 上下文未注入")
            return

        try:
            # 解析 JSON 字符串
            context = json.loads(event.angelheart_context)

            # 提取各个字段
            chat_records = context.get('chat_records', [])
            secretary_decision = context.get('secretary_decision', {})
            needs_search = context.get('needs_search', False)

            # 处理多模态内容
            for record in chat_records:
                content = record.get('content', [])
                if isinstance(content, list):  # 多模态格式
                    text_parts = [item.get('text', '') for item in content if item.get('type') == 'text']
                    full_text = ''.join(text_parts)
                    logger.info(f"{record.get('sender_name', '未知')}: {full_text[:50]}...")
                else:  # 兼容旧的字符串格式
                    logger.info(f"{record.get('sender_name', '未知')}: {content[:50]}...")

            logger.info(f"收到 {len(chat_records)} 条聊天记录")
            logger.info(f"秘书决策: {secretary_decision.get('reply_strategy', '未知')}")

        except json.JSONDecodeError as e:
            logger.error(f"解析 AngelHeart 上下文失败: {e}")
        except Exception as e:
            logger.error(f"处理 AngelHeart 上下文时出错: {e}")
```

### 安全访问模式

为了避免解析错误，推荐使用更安全的访问模式：

```python
@on_llm_request()
async def safe_access_context(self, event: AstrMessageEvent, request: ProviderRequest):
    """安全访问 AngelHeart 上下文"""

    def get_context_safely():
        if not hasattr(event, 'angelheart_context'):
            return None
        try:
            return json.loads(event.angelheart_context)
        except (json.JSONDecodeError, TypeError):
            logger.warning("AngelHeart 上下文解析失败")
            return None

    context = get_context_safely()
    if not context:
        return

    # 使用上下文数据进行逻辑处理
    decision = context.get('secretary_decision', {})
    if decision.get('should_reply', False):
        # 执行回复逻辑
        pass
```

## 使用场景示例

### 示例1：条件性提示增强

根据秘书决策动态调整系统提示：

```python
@on_llm_request()
async def conditional_prompt_enhancement(self, event: AstrMessageEvent, request: ProviderRequest):
    """根据秘书决策条件性增强提示"""

    context = self._get_angelheart_context(event)
    if not context:
        return

    decision = context.get('secretary_decision', {})
    persona_name = decision.get('persona_name', '')

    if persona_name:
        # 添加人格信息到系统提示
        personality_prompt = f"你现在扮演 {persona_name} 的角色。"
        if request.system_prompt:
            request.system_prompt += f"\n{personality_prompt}"
        else:
            request.system_prompt = personality_prompt
```

### 示例2：搜索需求处理

在需要搜索时添加搜索工具调用：

```python
@on_llm_request()
async def handle_search_requirement(self, event: AstrMessageEvent, request: ProviderRequest):
    """处理搜索需求"""

    context = self._get_angelheart_context(event)
    if not context:
        return

    needs_search = context.get('needs_search', False)
    decision = context.get('secretary_decision', {})

    if needs_search or decision.get('needs_search', False):
        # 添加搜索相关的系统指令
        search_instruction = "如果需要查找信息，请使用可用的搜索工具。"
        if request.system_prompt:
            request.system_prompt += f"\n{search_instruction}"
        else:
            request.system_prompt = search_instruction

        logger.info("检测到搜索需求，已添加搜索指令")
```

### 示例3：上下文感知的回复过滤

根据对话话题过滤或修改回复：

```python
@filter.on_llm_response()
async def context_aware_response_filter(self, event: AstrMessageEvent, response: LLMResponse):
    """基于上下文过滤回复内容"""

    context = self._get_angelheart_context(event)
    if not context:
        return

    decision = context.get('secretary_decision', {})
    topic = decision.get('topic', '')

    # 根据话题过滤敏感内容
    if topic.lower() in ['政治', '争议性话题']:
        # 检查回复是否包含敏感内容
        sensitive_words = ['敏感词1', '敏感词2']
        for word in sensitive_words:
            if word in response.content:
                logger.warning(f"检测到敏感内容，已过滤回复")
                response.content = "抱歉，这个话题我不方便讨论。"
                break
```

### 示例4：统计和监控

收集使用统计信息：

```python
@filter.after_message_sent()
async def collect_statistics(self, event: AstrMessageEvent):
    """收集 AngelHeart 使用统计"""

    context = self._get_angelheart_context(event)
    if not context:
        return

    decision = context.get('secretary_decision', {})

    # 统计回复类型
    if decision.get('should_reply', False):
        strategy = decision.get('reply_strategy', '未知')
        self.reply_stats[strategy] = self.reply_stats.get(strategy, 0) + 1

        # 记录搜索使用情况
        if decision.get('needs_search', False):
            self.search_count += 1

        logger.debug(f"AngelHeart 回复统计已更新: {strategy}")
```

## 最佳实践

1. **空值检查**: 始终检查 `angelheart_context` 是否存在且有效
2. **异常处理**: 使用 try-catch 块处理 JSON 解析异常
3. **内容格式兼容**: 支持多模态内容格式（列表）和旧字符串格式，确保向后兼容
4. **性能考虑**: 避免在高频事件中进行复杂处理
5. **日志记录**: 记录上下文的使用情况，便于调试
6. **实体信息利用**: 合理使用 `entities`、`facts` 和 `keywords` 字段来增强下游逻辑

## 注意事项

- 上下文注入仅在 AngelHeart 插件活跃时发生
- JSON 解析失败时会记录错误日志，但不会中断事件流
- 上下文数据仅在当前事件生命周期内有效
- 多个插件可以同时访问相同的上下文数据
- 建议在 `@filter.on_llm_request()` 中使用上下文，因为这是 LLM 调用的关键时机

通过合理利用 AngelHeart 注入的上下文，其他插件可以实现更智能、更个性化的响应逻辑。