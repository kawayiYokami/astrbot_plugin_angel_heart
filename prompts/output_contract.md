# 输出契约

## 输出

### 字段定义

- `should_reply`：boolean，是否需要介入。
- `is_questioned`：boolean，是否被追问（用户在继续之前的话题或要求回应之前的回答）。
- `is_interesting`：boolean，话题是否有趣（符合 AI 身份、能提供价值、介入合适）。
- `reply_strategy`：string，概述你计划采用的策略；`should_reply` 为 `false` 时填「继续观察」。
- `topic`：string，对当前唯一核心话题的简要概括，禁止把名字写进话题。
- `reply_target`：string，回复目标用户的昵称或 ID；不需要回复时填空字符串。

### 检索字段

- `entities`：优先级最高的是发言人 ID，其次是对话中的其他实体（包含但不限于人物、话题、物品、时间、地点、活动等）。不要把整句话当实体。
- `facts`：极简日志模式，只保留「谁 做了 什么」或「谁 提议 什么」，单句不超过 15 个字，禁止形容词。
- `keywords`：1-3 个核心搜索词。

这些字段是 RAG（检索增强生成）系统的检索词，用于匹配相关历史对话和知识库内容。

### 输出格式

{output_report_rule}

### 输出示例

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
