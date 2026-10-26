# 消息管理与上下文压缩

## 功能目标

对齐 PAI 语义：`当前摘要 + 当前连续消息块`。  
ledger 为内存态唯一真相源。

## 触发时机

### 私聊

- token / 遗忘阈值触发
- **主动 LLM 生成摘要**
- 失败则安全规则回退
- 工具消息可进入保留/摘要

### 群聊

- 离场→在场：入场规则整理，始终清工具，不保留离场前链路
- 体积超限：规则整理；未超时则保留尾部 `tool_retain_tokens` 内工具，超时才全清
- **不主动 LLM 摘要**

## 核心流程

```text
整理开始
→ 上锁
→ 生成/规则收口摘要
→ 原子提交：current_summary + 连续块
→ 解锁
```

整理与防抖解耦：整理期间不再关闭防抖。

## 读取规则

正式上下文：

```text
当前摘要
→ 当前连续消息块
→ 结束边界
```

`is_processed` 已退役，不再参与切分。

秘书轻量窗口仍可在连续块上取最近有限消息，但不得另造第二套已处理水位。群聊助理用同边界完整记录重算（`partition_dialogue_raw`），同边界不扩窗，以便下一轮追溯工具结果里的路径。

摘要折叠图片消息时，摘要正文补上该图片的本地缓存路径，并让摘要消息持有这些路径的引用；摘要再次被整理时引用继续继承。

## 状态归属

- `ConversationLedger.current_summary`
- `ConversationLedger.messages`（当前连续块）
- 压缩锁：`_compression_locks[chat_id]`
- 媒体清理门闩：会话仍在调度中（防抖记录、秘书单飞、运行中的工作任一存在）时只登记待清理、不删文件，等调度收口后再补做清理

## 规则与计量

1. 群聊平时 token 触发只保留尾部工具；`forgetting_timeout`（默认 86400 秒）超时才全清
2. `organize_on_group_enter` 始终全清工具
3. 私聊工具有价值，可进入保留/摘要
4. 压缩可上锁，半成品不可读
5. 入场整理带 `keep_from_timestamp` 时，不回退成“最近 N 条”
6. AstrBot 官方历史只在重启冷启动补种
7. 入场整理与冷启动补种互斥：做过入场整理后，激活路径禁止再补种

## 异常降级

- 私聊 LLM 失败 → 规则回退，不提交空半成品
- 重复整理抢锁失败 → 跳过
- 调度中延迟媒体清理失败 → 等会话空闲后再补做，不在运行中请求上删文件

## 模块关系

```text
FrontDesk 私聊缓存 → maybe_llm_compress_private
FrontDesk 群聊入场 → organize_on_group_enter
ConversationLedger.organize_context
→ partition_dialogue / partition_dialogue_raw 注入摘要前缀
```
