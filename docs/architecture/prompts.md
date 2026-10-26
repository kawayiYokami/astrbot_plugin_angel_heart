# 秘书分析提示词

秘书分析请求由三部分组成，都在 `prompts/`：

- `system_prompt.md`：群聊参与判定
- `output_contract.md`：输出字段与格式，固定不变
- `conversation_template.md`：待分析的对话记录模板，固定不变

人格不再由插件自建。会话人格来自 AstrBot 人格系统，作为分析请求的 **system**；插件规则作为 **第一条用户消息**。

## 组装方式

`LLMAnalyzer._build_prompts` 产出两条用户消息：

1. **规则文本**：参与判定 + 输出契约。判定正文按 `PromptSettingsStore` 二选一：预设为 `system_prompt.md` 全文，自定义为用户填写内容；自定义为空时回落预设。`{alias}` 按该群配置替换。
2. **对话记录**：把历史上下文和最近对话填进 `conversation_template.md`，分别包在 `<已回应消息>` / `<未回应消息>`。若有工作账本，以第三人称追加在对话记录后，提醒秘书不要让助理处理重复问题。

`LLMAnalyzer._call_ai_model` 再把会话人格放进 `system_prompt`，把规则文本放进 `contexts` 的第一条用户消息，对话记录作为当前 `prompt`。

## 输出契约

秘书必须产出 `SecretaryDecision`：

- `should_reply`
- `is_questioned`
- `is_interesting`
- `reply_strategy`
- `topic`
- `reply_target`
- `entities` / `facts` / `keywords`

这些字段给旁路注入和检索线索用，不进主脑请求体。
