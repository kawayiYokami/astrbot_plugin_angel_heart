# AngelHeart 群聊双防抖与状态机

## 功能目标

把 AngelHeart 群聊参与模型对齐 PAI / easy_call_ai 的语义，并适配 AstrBot 事件流：

- 私聊 / 群聊两套状态机
- 群聊只有离场 / 在场
- 双防抖：助理防抖 + 秘书防抖
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
   → 旧事件 KILL
   → 最后边界事件等待到期
   → 重建上下文
   → 秘书决策
   → 需要回复则激活该事件进入主脑（独立子代理）
```

### 群聊规则

1. 离场 + 未唤醒：只入库
2. 离场 + 唤醒：进场，并建立该群友助理防抖
3. 在场 + 无助理防抖 + 未唤醒：建立秘书防抖
4. 有任意助理防抖：不建立秘书防抖
5. 助理防抖中再次唤醒：加速该群友助理防抖
6. 秘书防抖中被唤醒：加速秘书防抖，并标记必须回应
7. 同一群友助理防抖期间后续消息更新边界，无需再次唤醒

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
- 混脸熟不再进场；只有别人主动叫才进场

### 防抖账本

- 助理防抖：`(chat_id, sender_id)` 最多 1 条
- 秘书防抖：`chat_id` 最多 1 条
- 账本自管，不拥有消息正文
- 扣押只保存事件 Future、版本、边界、是否必须回应

## 规则与计量

1. 助理防抖默认 1 秒：`assistant_debounce_time`
2. 秘书防抖默认 7 秒：`secretary_debounce_time`
3. 加速默认 1 秒：`accelerate_debounce_time`
4. 在场超时：`observation_timeout`
5. 激活时必须重建上下文，不得沿用事件创建时快照
6. 每个被激活事件天然对应一个独立子代理 / 远程应答
7. 可并发的是多个被激活事件；不能做的是向已运行子代理注入新消息

## 异常降级

1. 闭嘴 / 掌嘴：清理该会话全部防抖，旧事件 KILL
2. 在场超时：转离场，清理防抖与决策缓存
3. 防抖计时异常：对应事件 KILL，不激活
4. 激活后无新消息：不回复
5. 秘书分析失败：不回复，停止事件

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
