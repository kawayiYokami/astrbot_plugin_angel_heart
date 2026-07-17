# AngelHeart 群聊双防抖与状态机

## 功能目标

把 AngelHeart 群聊参与模型对齐 PAI / easy_call_ai 的语义，并适配 AstrBot 事件流：

- 私聊 / 群聊两套状态机
- 群聊只有离场 / 在场
- 双防抖：助理防抖 + 秘书防抖
- 同会话秘书单飞与回复/不回复冷却
- 防抖是目的，扣押是实现
- 激活时重建上下文
- 一事件一子代理

## 触发时机

### 私聊

1. 任意有效私聊消息入库
2. 不进入秘书
3. 不进入双防抖
4. 由 AstrBot 主框架队列 / 直接响应

### 群聊

1. 消息成功写入自建历史后
2. 以前台当前事件为单位判断是否唤醒
3. 更新双防抖账本，并把等待挂在该事件上
4. 防抖到期后只放行最后边界事件

## 核心流程

```text
消息事件到来
→ 缓存到 ConversationLedger
→ 私聊：结束（只缓存，主框架队列）
→ 群聊：
   → 当前事件是否唤醒
   → 更新助理/秘书防抖账本
   → 旧事件 KILL，只保留最后边界事件
   → 到期时先检查同会话秘书调度；秘书防抖再检查冷却门闩
   → 被门闩阻断则完整重计一轮
   → 放行最后边界事件，重建上下文
   → 秘书决策
   → 需要回复则激活该事件进入主脑（独立子代理）
   → 发送成功或不回复后收口秘书调度
```

### 群聊规则

1. 离场 + 未唤醒：只入库
2. 离场 + 唤醒：进场，并建立该群友助理防抖
3. 在场 + 无助理防抖 + 未唤醒：建立秘书防抖
4. 有任意助理防抖：不建立秘书防抖
5. 助理防抖中再次唤醒：加速该群友助理防抖
6. 秘书防抖中被唤醒：加速秘书防抖，并标记必须回应
7. 同一群友助理防抖期间后续消息更新边界，无需再次唤醒
8. 同一 `chat_id` 从秘书分析到发送成功、不回复或异常收口，最多只有一轮秘书调度
9. `waiting_time` 或 `no_reply_cooldown` 未结束时，秘书防抖到期保留最后边界事件并完整重计

## 状态归属

### 私聊

- 无离场 / 在场
- 无秘书
- 无引导
- 忙碌时只能队列

根因：AstrBot 无法在已运行子代理中注入消息。

### 群聊

- `NOT_PRESENT` = 离场
- `OBSERVATION` = 在场
- `SUMMONED` / `GETTING_FAMILIAR` 仅兼容旧路径，不再作为进场条件
- 复读、跟风或密集聊天不再进场；只有当前事件明确唤醒时才进场

### 防抖账本

- 助理防抖：`(chat_id, sender_id)` 最多 1 条
- 秘书防抖：`chat_id` 最多 1 条
- 秘书调度：`chat_id` 最多 1 轮，从秘书分析持续到发送/不回复/异常收口
- 回复后冷却：`waiting_time`
- 不回复冷却：`no_reply_cooldown`
- 账本自管，不拥有消息正文
- 扣押只保存事件 Future、版本、边界、是否必须回应

## 规则与计量

1. 助理防抖默认 1 秒：`assistant_debounce_time`
2. 秘书防抖默认 7 秒：`secretary_debounce_time`
3. 加速默认 1 秒：`accelerate_debounce_time`
4. 回复后冷却：`waiting_time`；不回复冷却：`no_reply_cooldown`
5. 在场超时：`observation_timeout`
6. 激活时必须重建上下文，不得沿用事件创建时快照
7. 同一会话只能有一个运行中的秘书调度；不同会话可以各自独立调度
8. 不能向已运行子代理注入后到消息

## 异常降级

1. 闭嘴 / 掌嘴：清理该会话全部防抖，旧事件 KILL
2. 在场超时：转离场，清理防抖与决策缓存
3. 防抖计时异常：对应事件 KILL，不激活
4. 激活后无新消息：不回复并收口秘书调度
5. 秘书分析失败：停止事件并收口秘书调度

## 模块关系

```text
main.smart_reply_handler
→ FrontDesk.handle_event
   → cache_message / ConversationLedger
   → DebounceManager.schedule
   → 到期放行最后边界事件
   → FrontDesk._activate_group_event
      → 重建上下文
      → Secretary.handle_message_by_state
      → 需要回复则唤醒主脑（该事件独立子代理）
```

关键文件：

- `core/debounce_manager.py`
- `roles/front_desk.py`
- `roles/secretary.py`
- `core/angel_heart_status.py`
- `core/angel_heart_context.py`
- `core/conversation_ledger.py`
