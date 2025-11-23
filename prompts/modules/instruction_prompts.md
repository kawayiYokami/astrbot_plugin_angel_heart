# 指令提示模块

## 输出字段定义

### 核心字段
- `should_reply`：(boolean) 是否需要介入。
- `is_questioned`：(boolean) 是否被追问（用户在继续之前的话题或要求回应之前的回答）。
- `is_interesting`：(boolean) 话题是否有趣（符合AI身份、能提供价值、介入合适）。
- `reply_strategy`：(string) 概述你计划采用的策略。如果 `should_reply` 为 `false`，此项应为 "继续观察"。
- `topic`：(string) 对当前唯一核心话题的简要概括。
- `reply_target`：(string) 回复目标用户的昵称或ID。如果不需要回复，此项应为空字符串。

### RAG检索字段
- `entities`：**【优先级最高：发言人 ID】**，其次是其他对话中的实体（包含但不限于人物、话题、物品、时间、地点、活动等）。（不要把整句话当实体）
- `facts`：**【极简日志模式】**。只保留"谁 做了 什么"或"谁 提议 什么"。**单句禁止超过 15 个字**。禁止形容词。
- `keywords`：1-3个核心搜索词。

## JSON版本输出要求
直接输出以下 JSON 对象，不需要任何分析报告或思考过程。

### 输出示例：
```json
{
  "should_reply": true,
  "is_questioned": true,
  "is_interesting": true,
  "reply_strategy": "提供技术解决方案",
  "topic": "Python代码调试",
  "reply_target": "小明",
  "entities": ["小明", "小红", "Python", "代码调试"],
  "facts": ["小明询问代码调试", "小红遇到问题"],
  "keywords": ["Python调试", "代码问题"]
}
```

### RAG检索字段用途说明
这些字段将作为**RAG（检索增强生成）系统的检索词**，用于匹配相关历史对话和知识库内容。

## 待分析的对话记录模板

### 历史对话参考（仅供了解长期背景，你不需要对这些内容做出回应，也不需要对这些对话进行分析）
---
{historical_context}
---

### 需要你分析的最新对话（这是你的主要分析对象）
---
{recent_dialogue}
---