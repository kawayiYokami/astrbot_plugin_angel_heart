# 群聊配置模板与 WebUI

未绑定的群使用全局配置。绑定模板后，该群的点名、离场应答、回复长度、能量、回复节奏改用模板值，不再随全局改动。

## 模板与绑定

`ChatProfileStore` 把模板和绑定持久化到插件数据目录的 `chat_profiles.json`。

模板只覆盖这五类分组（代码注释里的「助理画像」已不再进入模板，人格走 AstrBot 会话人格）：

- `wake_interaction`：仅点名入场、强制回复、别名、掌嘴/张嘴词
- `leave_reply`：复读 / 密集聊天离场应答
- `reply_length`：常规与焦点字数提醒
- `energy`：精力上下限、恢复与消耗
- `timing`：点名等待、巡检等待、加速、在场超时、助理休息

未知键写入时会被丢掉。绑定键是 `unified_msg_origin`；未绑定回退全局配置。`ConfigManager.for_chat(chat_id)` 是按群读取的入口。

系统提示词不进模板，也不进 AstrBot 自带设置页。

## 系统提示词设置

`PromptSettingsStore` 单独存 `prompt_settings.json`，全局一份：

- `preset`：使用 `prompts/system_prompt.md` 全文
- `custom`：使用用户填写文本；空文本回落到预设

同一时刻只有一种生效。WebUI 全局配置页提供预设 / 自定义切换。

## 联系人监控

插件页面「联系人监控」每 3 秒刷新：

- 在场 / 离场（群聊安静超过在场超时后，展示层不再显示过期在场）
- 剩余能量
- 巡检 / 等待进度
- 最近一次秘书决策
- 当前绑定的模板

支持群聊 / 私聊筛选。见过的会话来自 `ChatSourcesStore`。

## 页面与接口

- 源码：`webui/`
- 构建产物：`pages/chat-config/`
- 构建命令：在 `webui/` 下执行 `npm run build`
- 后端：`web_api/register_all_routes`，路径前缀 `/astrbot_plugin_angel_heart/`

主要接口：

- 模板 CRUD 与绑定
- 来源登记 / 状态仪表盘
- 全局配置读写（与 `_conf_schema.json` 同源）
- 系统提示词设置读写
