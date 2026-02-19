# AngelHeart 插件 - 项目上手指引

## 项目概述

AngelHeart 是一个专为 AstrBot 平台设计的智能群聊交互插件，采用创新的两级AI协作架构和4状态智能交互系统，实现高质量、低成本的智能对话交互。

## 核心设计模式

### 1. 两级AI协作架构

项目采用"轻量级AI（分析员）+ 重量级AI（专家）"的协作模式：

- **轻量级AI（分析员）**：由 [`LLMAnalyzer`](core/llm_analyzer.py:51) 实现，使用低成本模型实时分析对话，判断是否需要回复
- **重量级AI（专家）**：由 AstrBot 主框架提供，仅在必要时激活，生成高质量回复
- **智能决策注入**：通过 [`inject_oneshot_decision_on_llm_request()`](main.py:88) 将分析员决策动态注入到专家的提示词中

### 2. 4状态智能交互系统

通过 [`AngelHeartStatus`](core/angel_heart_status.py:16) 枚举实现的状态机：

- **NOT_PRESENT（不在场）**：初始状态，AI保持静默观察
- **SUMMONED（被呼唤）**：检测到@或关键词呼唤时进入准备响应状态
- **GETTING_FAMILIAR（混脸熟）**：检测到复读或密集讨论时主动参与
- **OBSERVATION（观测中）**：AI回复后进入观察期，避免频繁插话

状态转换由 [`StatusTransitionManager`](core/angel_heart_status.py:313) 管理，支持自动超时降级。

### 3. 事件扣押与门锁机制

实现并发控制的核心机制：

- **门牌管理**：通过 [`acquire_chat_processing()`](core/angel_heart_context.py:116) 和 [`release_chat_processing()`](core/angel_heart_context.py:174) 控制会话处理权
- **事件扣押**：通过 [`hold_and_start_observation()`](core/angel_heart_context.py:196) 实现事件排队和超时处理
- **耐心计时器**：通过 [`start_patience_timer()`](core/angel_heart_context.py:390) 提供用户等待反馈

### 4. 分层架构设计

项目采用清晰的分层结构：

- **角色层**：[`FrontDesk`](roles/front_desk.py:34)（前台）和 [`Secretary`](roles/secretary.py:33)（秘书）
- **核心层**：状态管理、上下文管理、消息处理等核心功能
- **工具层**：各种工具类和辅助函数
- **模型层**：数据模型定义

## 数据流与控制流

### 消息处理流程

1. **消息接收**：[`smart_reply_handler()`](main.py:74) 接收消息事件
2. **前置检查**：[`_should_process()`](main.py:348) 进行白名单、@消息等检查
3. **前台缓存**：[`FrontDesk.handle_event()`](roles/front_desk.py:166) 缓存消息并触发状态检查
4. **状态判断**：[`StatusChecker.determine_status()`](core/angel_heart_status.py:45) 决定当前状态
5. **秘书分析**：[`Secretary.handle_message_by_state()`](roles/secretary.py:64) 根据状态处理消息
6. **LLM分析**：[`LLMAnalyzer.analyze_and_decide()`](core/llm_analyzer.py:227) 调用轻量级AI分析
7. **决策执行**：根据 [`SecretaryDecision`](models/analysis_result.py:4) 决定是否回复
8. **上下文注入**：[`inject_oneshot_decision_on_llm_request()`](main.py:88) 注入决策到主框架
9. **主脑回复**：AstrBot 主框架生成最终回复

### 上下文管理流程

1. **消息存储**：[`ConversationLedger.add_message()`](core/conversation_ledger.py:181) 存储所有消息
2. **上下文快照**：[`get_context_snapshot()`](core/conversation_ledger.py:249) 生成分析用上下文
3. **图片处理**：[`generate_captions_for_chat()`](core/conversation_ledger.py:373) 为图片生成描述
4. **消息格式化**：[`MessageProcessor`](core/message_processor.py:21) 转换消息格式
5. **上下文注入**：通过 [`rewrite_prompt_for_llm()`](roles/front_desk.py:178) 重写提示词

## 场景化导航

### 新增业务逻辑

1. **新增状态处理逻辑**：
   - 在 [`Secretary.handle_message_by_state()`](roles/secretary.py:64) 中添加新的状态处理分支
   - 在 [`StatusChecker.determine_status()`](core/angel_heart_status.py:45) 中添加状态判断逻辑

2. **新增决策因素**：
   - 修改 [`LLMAnalyzer._build_prompt()`](core/llm_analyzer.py:186) 添加新的提示词内容
   - 更新 [`SecretaryDecision`](models/analysis_result.py:4) 模型添加新字段

3. **新增消息处理规则**：
   - 在 [`FrontDesk.handle_event()`](roles/front_desk.py:166) 中添加新的处理逻辑
   - 修改 [`MessageProcessor`](core/message_processor.py:21) 添加新的消息转换规则

### 修改数据定义

1. **修改消息结构**：
   - 更新 [`ConversationLedger.add_message()`](core/conversation_ledger.py:181) 中的消息格式
   - 同步修改 [`MessageProcessor`](core/message_processor.py:21) 中的处理逻辑

2. **修改配置项**：
   - 在 [`ConfigManager`](core/config_manager.py:7) 中添加新的配置属性
   - 更新 [`_conf_schema.json`](_conf_schema.json:1) 添加配置定义

3. **修改状态定义**：
   - 更新 [`AngelHeartStatus`](core/angel_heart_status.py:16) 枚举
   - 同步修改 [`StatusTransitionManager`](core/angel_heart_status.py:313) 中的转换逻辑

### 配置环境

1. **基础配置**：
   - 修改 [`ConfigManager`](core/config_manager.py:7) 中的默认值
   - 通过 AstrBot WebUI 修改配置

2. **模型配置**：
   - 设置 `analyzer_model` 指定轻量级分析模型
   - 配置 `is_reasoning_model` 适配思维模型

3. **群聊增强**：
   - 启用 `group_chat_enhancement` 使用增强上下文管理
   - 配置 `max_conversation_tokens` 控制上下文大小

## 关键技术栈

### 核心依赖

- **AstrBot框架**：提供基础插件系统和消息处理能力
- **异步编程**：基于 asyncio 实现高并发处理
- **SQLite**：用于图片转述缓存存储
- **PIL**：用于图片处理和哈希计算

### 关键组件

- **状态机**：基于枚举的状态转换系统
- **并发控制**：通过锁和Future实现的事件扣押机制
- **上下文管理**：分层的对话历史和上下文处理
- **多模态支持**：图片转述和多模态内容处理

## 开发注意事项

### 并发安全

1. **锁使用**：所有共享状态访问必须通过相应的锁保护
2. **原子操作**：状态转换和门锁操作必须是原子的
3. **异常处理**：确保在异常情况下正确释放资源

### 性能考虑

1. **消息限制**：通过 `PER_CHAT_LIMIT` 和 `TOTAL_MESSAGE_LIMIT` 控制内存使用
2. **缓存策略**：图片转述结果使用 dHash 缓存避免重复处理
3. **异步优化**：所有IO操作必须异步执行

### 兼容性

1. **模型适配**：支持不同类型的LLM模型（常规/思维模型）
2. **消息格式**：兼容纯文本和多模态消息
3. **平台适配**：适配不同聊天平台的消息格式

## 扩展点

### 自定义触发器

通过 [`ProactiveManager`](core/proactive_manager.py:67) 的 `register_custom_trigger()` 方法注册自定义触发逻辑：

```python
async def custom_trigger(chat_id: str, context_data: Dict) -> bool:
    # 自定义触发逻辑
    return True

angel_context.proactive_manager.register_custom_trigger("my_trigger", custom_trigger)
```

### 提示词模块

通过修改 [`prompts/modules/`](prompts/modules/) 目录下的Markdown文件自定义AI行为：

- [`identity.md`](prompts/modules/identity.md)：AI身份定义
- [`behavior_rules.md`](prompts/modules/behavior_rules.md)：行为规则
- [`decision_logic.md`](prompts/modules/decision_logic.md)：决策逻辑

### 工具修饰

通过配置 `tool_decorations` 为工具调用添加拟人化提示：

```json
{
  "search": "我搜索一下|我查查|让我搜搜",
  "memory": "嗯让我想想|稍等",
  "python": "让我算算|计算中...|稍等我写个代码"
}
```

## 调试与监控

### 日志系统

项目使用结构化日志，关键日志标识：

- `AngelHeart[{chat_id}]`：会话相关日志
- `AngelHeart分析器`：LLM分析器相关日志
- `AngelHeart: `：系统级日志

### 状态监控

通过 [`StatusTransitionManager.get_status_summary()`](core/angel_heart_status.py:424) 获取状态摘要：

```python
summary = angel_context.status_transition_manager.get_status_summary(chat_id)
print(f"当前状态: {summary['current_status']}")
print(f"持续时间: {summary['duration_seconds']}秒")
```

### 决策缓存

通过 [`AngelHeartContext.get_decision()`](core/angel_heart_context.py:437) 获取最新决策：

```python
decision = angel_context.get_decision(chat_id)
if decision:
    print(f"决策: {'回复' if decision.should_reply else '不回复'}")
    print(f"策略: {decision.reply_strategy}")
    print(f"话题: {decision.topic}")
```

## 常见问题

### Q: 如何添加新的状态？

A: 1. 在 [`AngelHeartStatus`](core/angel_heart_status.py:16) 枚举中添加新状态
   2. 在 [`StatusChecker.determine_status()`](core/angel_heart_status.py:45) 中添加判断逻辑
   3. 在 [`Secretary.handle_message_by_state()`](roles/secretary.py:64) 中添加处理方法
   4. 在 [`StatusTransitionManager`](core/angel_heart_status.py:313) 中添加转换逻辑

### Q: 如何自定义消息处理逻辑？

A: 1. 修改 [`FrontDesk.handle_event()`](roles/front_desk.py:166) 添加新的处理分支
   2. 或通过 [`ProactiveManager`](core/proactive_manager.py:67) 注册自定义触发器
   3. 或修改 [`MessageProcessor`](core/message_processor.py:21) 自定义消息转换

### Q: 如何优化性能？

A: 1. 调整 `max_conversation_tokens` 限制上下文大小
   2. 调整 `cache_expiry` 控制缓存时间
   3. 使用更轻量的分析模型
   4. 启用图片转述缓存

### Q: 如何处理特殊消息格式？

A: 1. 在 [`MessageProcessor`](core/message_processor.py:21) 中添加特殊格式处理
   2. 修改 [`FrontDesk.cache_message()`](roles/front_desk.py:65) 自定义缓存逻辑
   3. 更新 [`ConversationLedger`](core/conversation_ledger.py:19) 的消息存储格式

---

通过理解以上架构和设计模式，您可以快速上手 AngelHeart 插件的开发和定制。项目的模块化设计和清晰的分层结构使得扩展和维护变得相对简单。

## 提交与版本号规范

### 版本号更新规则

1. 插件发布版本号只在 [`metadata.yaml`](metadata.yaml:1) 的 `version` 字段维护。
2. 不在 `main.py` 的 `@register(...)` 中手动改版本号，避免双处维护导致不一致。
3. 版本号建议使用补丁递增（如 `0.8.11 -> 0.8.12`），功能性变更再按语义化规则提升次版本。

### Commit 流程（标准）

1. 完成功能修改后，先做基础检查（至少执行一次 `python -m compileall .` 或等效检查）。
2. 查看变更：`git status --short`、`git diff`。
3. 只提交本次相关文件：`git add <files...>`。
4. 提交信息建议格式：`type(scope): summary`
   - 示例：`fix(lock): ensure lock release on exception paths`
   - 常用 type：`fix`、`feat`、`refactor`、`docs`、`chore`
5. 提交：`git commit -m "<message>"`。
6. 若是发布提交，确认 `metadata.yaml` 已更新为目标版本后再提交。
