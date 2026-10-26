# AngelHeart 插件 - 项目上手指引

## 项目概述

AngelHeart 是一个专为 AstrBot 平台设计的智能群聊/私聊交互插件。现行模型是前台 / 秘书 / 助理三角色、离场/在场两态与双防抖调度。发布版本以 [`metadata.yaml`](metadata.yaml) 为准。

## 核心设计模式

### 1. 三角色协作

- **前台**：[`FrontDesk`](roles/front_desk.py:53)，纯逻辑。入账、分流、激活、重写主脑请求。
- **秘书**：[`Secretary`](roles/secretary.py:21) + [`LLMAnalyzer`](core/llm_analyzer.py:51)，轻量模型，只判断是否接话。
- **助理**：AstrBot 主框架提供的主模型，仅在秘书放行后生成回复。

秘书决策不进主脑请求体。`event.angelheart_context` 只给日志和下游钩子看。

### 2. 两态参与模型与双防抖

群聊只以 [`AngelHeartStatus`](core/angel_heart_status.py:29) 的两种现行语义参与：

- **NOT_PRESENT（离场）**：只缓存消息；默认（开启「仅点名入场」）只有 @ 或唤醒词命中时才能进场，关闭「仅点名入场」后任何消息都会进场处理。复读、跟风和密集聊天不触发进场；开启离场应答后，复读或密集聊天只回复一次，回复后仍离场。
- **OBSERVATION（在场）**：由点名进入或回复后保持；在场超时后回到离场。

`SUMMONED` 与 `GETTING_FAMILIAR` 只为旧数据/旧调用兼容保留，不是现行状态机的一部分，禁止以它们新增业务分支。

群聊调度由 [`DebounceManager`](core/debounce_manager.py:62) 管理：助理防抖处理点名，秘书防抖处理在场的普通消息；旧事件 KILL，只放行最后边界事件。同一会话从秘书分析到发送或不回复收口，最多存在一轮秘书调度。交给助理后立即释放单飞。

### 3. 分层架构

- **角色层**：`FrontDesk`、`Secretary`
- **核心层**：状态、账本、防抖、配置、工作账本、群模板
- **工具层**：`angel_describe_image`
- **模型层**：`SecretaryDecision`
- **界面层**：`webui/` 源码，构建产物在 `pages/chat-config/`，接口在 `web_api/`

## 数据流与控制流

### 消息处理流程

1. **消息接收**：[`smart_reply_handler()`](main.py:147) 接收消息事件。
2. **前置检查**：[`_should_process()`](main.py:364) 处理白名单、上游命令等边界。
3. **前台入账**：[`FrontDesk.handle_event()`](roles/front_desk.py:476) 将有效消息写入 `ConversationLedger`。
4. **群聊分流**：私聊只入账并交给 AstrBot 队列；群聊由 [`_schedule_group_debounce()`](roles/front_desk.py:584) 依据当前事件是否点名、会话是否在场更新防抖账本。
5. **防抖放行**：[`DebounceManager.schedule()`](core/debounce_manager.py:427) KILL 被替换的旧事件，只放行最后边界事件；同会话冷却、助理忙碌或已有秘书调度时，完整重计等待。
6. **上下文重建**：[`_activate_group_event()`](roles/front_desk.py:671) 在放行后按账本重新读取当前上下文，并登记工作账本。
7. **秘书分析**：[`Secretary.handle_message_by_state()`](roles/secretary.py:46) 由轻量模型决定是否回复。
8. **主脑回复**：`should_reply=True` 才让 AstrBot 主框架继续处理该事件。仅 @ 无正文时由前台补 `message_str`。
9. **完成收口**：发送成功、不回复或异常路径释放同会话秘书调度；助理被调用后进入 `waiting_time` 休息。工作账本 `running` 超时默认 300 秒，超时惰性标记 failed，解除 `assistant_busy`。

### 上下文管理流程

1. **消息存储**：[`ConversationLedger.add_message()`](core/conversation_ledger.py:378) 存储所有消息。
2. **上下文快照**：[`get_context_snapshot()`](core/conversation_ledger.py:467) 生成分析用上下文。
3. **消息格式化**：[`MessageProcessor`](core/message_processor.py:22) 转换消息格式。
4. **上下文注入**：通过 [`rewrite_prompt_for_llm()`](roles/front_desk.py:2230) 重写提示词。群聊钉在秘书判断点；私聊从账本读取。工作账本只作临时提醒。
5. **按需图片**：主模型需要细节时调用 [`angel_describe_image`](tools/image_understanding.py:11)。不要把 `generate_captions_for_chat()` 当成现行自动转述入口。

## 场景化导航

### 新增业务逻辑

1. **调整群聊参与规则**：
   - 优先检查 [`FrontDesk._schedule_group_debounce()`](roles/front_desk.py:584) 与 [`DebounceManager.schedule()`](core/debounce_manager.py:427) 的完整路径。
   - 点名判定改 [`StatusChecker.is_event_wake()`](core/angel_heart_status.py:238)；普通消息、冷却和同会话单飞规则改 `DebounceManager`。
   - 不要把复读、跟风或密集发言重新接为离场进场条件；离场应答只能一次性回复，回复后保持离场。

2. **新增决策因素**：
   - 修改 [`LLMAnalyzer._build_prompts()`](core/llm_analyzer.py:230) 添加新的提示词内容。
   - 更新 [`SecretaryDecision`](models/analysis_result.py:4) 模型添加新字段。

3. **新增消息处理规则**：
   - 在 [`FrontDesk.handle_event()`](roles/front_desk.py:476) 中添加新的处理逻辑。
   - 修改 [`MessageProcessor`](core/message_processor.py:22) 添加新的消息转换规则。

### 修改数据定义

1. **修改消息结构**：
   - 更新 [`ConversationLedger.add_message()`](core/conversation_ledger.py:378) 中的消息格式。
   - 同步修改 [`MessageProcessor`](core/message_processor.py:22) 中的处理逻辑。

2. **修改配置项**：
   - 在 [`ConfigManager`](core/config_manager.py:18) 中添加新的配置属性。
   - 更新 [`_conf_schema.json`](_conf_schema.json) 添加配置定义。
   - 若该字段允许按群覆盖，同步更新 [`ChatProfileStore.TEMPLATE_GROUPS`](core/chat_profile.py:19)。

3. **修改参与状态定义**：
   - 群聊当前只认 `NOT_PRESENT` 与 `OBSERVATION`；修改这两态语义属于状态机架构变更。
   - 必须同步检查 [`AngelHeartStatus`](core/angel_heart_status.py:29)、[`StatusTransitionManager`](core/angel_heart_status.py:470)、`FrontDesk` 调度路径与架构文档。

### 配置环境

1. **基础配置**：修改 `ConfigManager` 默认值，或通过 AstrBot / 插件 WebUI 修改。
2. **模型配置**：`analyzer_model` 指定秘书模型；`is_reasoning_model` 适配思维模型。
3. **群聊增强**：启用 `group_chat_enhancement` 接管群聊上下文；`max_conversation_tokens` 控制上下文大小。
4. **群模板**：通过插件 WebUI 创建模板并绑定 `chat_id`；未绑定回退全局。
5. **系统提示词**：预设 / 自定义二选一，存在 `PromptSettingsStore`，不进 AstrBot 自带设置页。

## 关键技术栈

- **AstrBot 框架**：插件系统、事件流、主模型
- **异步编程**：asyncio
- **SQLite**：图片转述缓存
- **PIL**：图片处理和哈希计算
- **Vue 3 + naive-ui + Vite**：插件 WebUI

## 开发注意事项

### 并发安全

1. 共享状态访问必须通过相应的锁保护。
2. 状态转换与防抖账本操作必须是原子的。
3. 异常路径必须释放秘书单飞和工作账本。

### 性能考虑

1. 通过上下文整理（token / 超时）与 `TOTAL_MESSAGE_LIMIT` 控制内存。
2. 图片转述结果使用 dHash 缓存。
3. 所有 IO 必须异步执行。
4. 会话仍在调度中时延迟媒体清理，避免删掉运行中请求正在引用的图片。

### 兼容性

1. 支持常规 / 思维模型。
2. 兼容纯文本和多模态消息。
3. 适配不同聊天平台的消息格式。
4. Web API 优先 `astrbot.api.web`，旧版回退 quart。

## 测试规范

### 测试环境探查（硬性前置）

1. 跑任何测试前先探查部署环境：先 `docker ps` 确认是否存在 astrbot 容器。
2. 存在 astrbot 容器时，必须用 docker 测试，标准命令（容器名、路径以 `docker ps` 与 `docker inspect` 实际输出为准）：

   ```
   docker exec astrbot sh -c "cd /AstrBot && python -m pytest data/plugins/astrbot_plugin_angel_heart/tests -q"
   ```

3. 禁止宿主机直跑 pytest：不探查部署环境就自行模拟测试环境是错误路径——宿主机环境与容器不符，会超时或产生假结果，掩盖真实问题。

测试桩统一放在 [`tests/conftest.py`](tests/conftest.py)。

## 扩展点

### 自定义触发器

通过 [`ProactiveManager.register_custom_trigger()`](core/proactive_manager.py:249) 注册：

```python
async def custom_trigger(chat_id: str, context_data: Dict) -> bool:
    return True

angel_context.proactive_manager.register_custom_trigger("my_trigger", custom_trigger)
```

主动应答只在离场时触发。真正发消息由回调或调用方完成。

### 提示词文档

分析提示词由三份文档组成，均位于 [`prompts/`](prompts/)：

- [`system_prompt.md`](prompts/system_prompt.md)：群聊参与判定，作为第一条用户提示词（预设固定文本 / 自定义二选一，见 `core/prompt_settings.py`）
- [`output_contract.md`](prompts/output_contract.md)：输出字段与格式要求，固定不变
- [`conversation_template.md`](prompts/conversation_template.md)：待分析的对话记录模板，固定不变

会话人格来自 AstrBot 人格系统，作为分析请求的 system。组装入口是 [`LLMAnalyzer._build_prompts()`](core/llm_analyzer.py:230)。

## 调试与监控

### 日志系统

- `AngelHeart[{chat_id}]`：会话相关日志
- `AngelHeart分析器`：LLM 分析器相关日志
- `AngelHeart: `：系统级日志

### 状态监控

通过 [`StatusTransitionManager.get_status_summary()`](core/angel_heart_status.py:547) 获取状态摘要。插件 WebUI「联系人监控」每 3 秒刷新在场/离场、能量、巡检和最近决策。

### 决策归属

1. **门闩**：只决定「回不回」
   - `should_reply=True` → 唤醒主脑
   - `should_reply=False` → 停止事件
2. **旁路**：挂到本事件 `event.angelheart_context`（`chat_records` + `secretary_decision`）
3. **不进主脑请求体**：`req.contexts` 临时注入只留工作账本和回复长度提醒；历史重写只认 `ConversationLedger`
4. **过时字段**：不再注入 `needs_search`

## 常见问题

### Q: 如何调整群聊参与行为？

A: 1. 先确认变更是否影响离场/在场两态或双防抖规则
   2. 调整 [`FrontDesk._schedule_group_debounce()`](roles/front_desk.py:584) 的进场与事件分流
   3. 调整 [`DebounceManager`](core/debounce_manager.py:62) 的等待、替换、冷却与单飞门闩
   4. 同步更新 [`docs/architecture/group_dual_debounce_state_machine.md`](docs/architecture/group_dual_debounce_state_machine.md) 和对应测试

### Q: 如何自定义消息处理逻辑？

A: 1. 修改 [`FrontDesk.handle_event()`](roles/front_desk.py:476) 添加新的处理分支
   2. 或通过 [`ProactiveManager`](core/proactive_manager.py:67) 注册自定义触发器
   3. 或修改 [`MessageProcessor`](core/message_processor.py:22) 自定义消息转换

### Q: 如何优化性能？

A: 1. 调整 `max_conversation_tokens` 限制上下文大小
   2. 调整 `cache_expiry` 控制缓存时间
   3. 使用更轻量的分析模型
   4. 启用图片转述缓存

### Q: 如何处理特殊消息格式？

A: 1. 在 [`MessageProcessor`](core/message_processor.py:22) 中添加特殊格式处理
   2. 修改 [`FrontDesk.cache_message()`](roles/front_desk.py:319) 自定义缓存逻辑
   3. 更新 [`ConversationLedger`](core/conversation_ledger.py:22) 的消息存储格式

## 提交与版本号规范

### 版本号更新规则

1. 插件发布版本号只在 [`metadata.yaml`](metadata.yaml) 的 `version` 字段维护。
2. 不在 `main.py` 的 `@register(...)` 中手动改版本号，避免双处维护导致不一致。
3. 版本号建议使用补丁递增（如 `0.8.11 -> 0.8.12`），功能性变更再按语义化规则提升次版本。

### Commit 流程（标准）

1. 完成功能修改后，先做基础检查（至少执行一次 `python -m compileall .` 或等效检查）。
2. 查看变更：`git status --short`、`git diff`。
3. 只提交本次相关文件：`git add <files...>`。
4. 提交信息建议格式：`type(scope): 中文摘要`
   - 示例：`fix(lock): 异常路径确保释放门锁`
   - 常用 type：`fix`、`feat`、`refactor`、`docs`、`chore`
   - summary 使用中文；type 与 scope 保持英文小写
5. 提交：`git commit -m "<message>"`。
6. 若是发布提交，确认 `metadata.yaml` 已更新为目标版本后再提交。

## 文档入口

现行机制总览：[`docs/angelheart_workflow.md`](docs/angelheart_workflow.md)。
