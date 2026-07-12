# 消息管理与上下文压缩

## 功能目标

对齐 PAI 语义：`当前摘要 + 当前连续消息块`。  
ledger 为内存态唯一真相源。

## 触发时机

### 私聊
- token / 遗忘阈值触发
- **主动 LLM 生成摘要**
- 失败则安全规则回退

### 群聊
- 离场→在场：入场规则整理
- 体积超限：规则整理
- **不主动 LLM 摘要**
- 工具消息可丢

## 核心流程

```text
整理开始
→ 上锁
→ 关闭该会话全部防抖
→ 生成/规则收口摘要
→ 原子提交：current_summary + 连续块
→ 解锁
```

## 读取规则

正式上下文：

```text
当前摘要
→ 当前连续消息块
→ 结束边界
```

`is_processed` 已退役，不再参与切分。

秘书轻量窗口仍可在连续块上取最近有限消息，但不得另造第二套已处理水位。

## 状态归属

- `ConversationLedger.current_summary`
- `ConversationLedger.messages`（当前连续块）
- 压缩锁：`_compression_locks[chat_id]`
- 整理前回调：`on_before_organize`（关防抖）

## 规则与计量

1. 群聊不记工具
2. 私聊工具有价值，可进入保留/摘要
3. 压缩可上锁，半成品不可读
4. 整理期间关闭全部防抖
5. AstrBot 官方历史只在重启冷启动补种

## 异常降级

- 私聊 LLM 失败 → 规则回退，不提交空半成品
- 重复整理抢锁失败 → 跳过
- 入场整理带 `keep_from_timestamp` 时，不回退成“最近 N 条”

## 模块关系

```text
FrontDesk 私聊缓存 → maybe_llm_compress_private
FrontDesk 群聊入场 → organize_on_group_enter
ConversationLedger.organize_context
→ partition_dialogue / partition_dialogue_raw 注入摘要前缀
```
