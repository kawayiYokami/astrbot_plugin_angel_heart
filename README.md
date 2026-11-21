# AngelHeart · 天使之心

![天使之心](logo.png)

**被点名才说话的群聊 AI——懂分寸，知进退。**

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![AstrBot Plugin](https://img.shields.io/badge/AstrBot-Plugin-blue)](https://github.com/Soulter/AstrBot)

她在群里是什么样？热闹的时候她安静听着，从不抢话；有人叫到她，她立刻接上，还会轻轻戳一下叫她的人；长文她不会直接刷屏，而是排成一张好看的长图发出来，连字体都能挑。聊久了她也会累，说得多就歇得久一点。

她更像个真正的群友，而不是每条消息都要插一句的复读机。

## 三个角色，各干各的

她不是那种自己判断、又自己开口的 AI。一条消息要过三道手：

- **前台**（不花钱）：把群聊整理成册，安排说话的节奏。
- **秘书**（轻量模型，很便宜）：看一眼气氛，决定这一句要不要接。觉得不必接，事情就到此为止——贵的模型根本不醒来。
- **助理**（你的主模型）：只在秘书放行后开口，负责把话说好听。

所以她既不会见话就接，也不会对着半句话作答。你打字的时候前台会等你说完，只让最后一句触发判断。

## 懂进退

她在群里只有两种状态：

- **离场**：默认安静潜水，只记不说话。只有被 @ 或叫到唤醒词才进场。也可以打开「离场应答」：群里刷复读、多人热聊的时候她冒出来回一句——只回一句，回完继续潜水。
- **在场**：被点名后加入聊天。之后接不接话，交给秘书看气氛；群聊安静一段时间，她自己就退场了，下次再叫她就好。

## 她也会累

说话是耗力气的，长篇大论尤其累。每次开口都会消耗能量，说得越多消耗越大；能量随时间慢慢恢复，耗光了她就先歇着，过一会儿又精神了。

- **点名永远有效**：再累也会应你的召唤（但会等上一轮说完，不抢麦）。
- **短回复恢复快**：两三句的短话耗能少，她更愿意常开口；长回复要多歇一会儿再说话。
- 具体数值都在「能量设置」里可调，默认值开箱即用。

## 长文不刷屏

回复超过字数阈值自动排成竖版长图再发，群里不再被长文刷屏。字体面板提供三款霞鹜字体随心切换——新晰黑、新致宋、文楷，没下载的点一下就自动下好；想用自己的字体，直接上传就行。

## 每个群可以不一样

人设、唤醒词、回复长度、能量、说话节奏，都能按群绑定配置模板；没绑定的群用全局默认。

插件页面里还有**联系人监控**：哪个群在场、剩多少能量、最近一次为什么回（或不回），三秒一刷尽收眼底。她最近十次「看到了什么、问了什么」的请求原文也随时可查——她说错话的时候，你可以直接翻她的记忆。

## 上下文与图片

- **群聊上下文接管**：完整对话连同发言人、时间戳一起交给助理，谁说的、什么时候说的，清清楚楚。私聊消息同样入册，由主框架直接回复。
- **按需图片理解**：模型需要看某张图的细节时才调用 `angel_describe_image`，不为每张图都付一次理解的钱。

## 天使五件套

同一只粉毛天使的五个能力化身：

| 插件 | 能力 |
|------|------|
| [天使之心](https://github.com/kawayiYokami/astrbot_plugin_angel_heart) | 智能群聊/私聊交互，懂分寸、知进退 |
| [天使之眼](https://github.com/kawayiYokami/astrbot_plugin_angel_eye) | 聊天记录查询 |
| [天使之笑](https://github.com/kawayiYokami/astrbot_plugin_angel_smile) | 表情包管理 |
| [天使之魂](https://github.com/kawayiYokami/astrbot_plugin_angel_memory) | AI 记忆与认知 |
| [天使的画笔](https://github.com/kawayiYokami/astrbot_plugin_angel_brush) | 说话出图、发图改图 |

## 快速开始

前置：AstrBot 已安装并运行，至少一个可用的模型（推荐再配一个便宜的轻量模型当秘书）。

1. **市场安装**：在 AstrBot 的插件市场里搜「天使之心」。
2. **手动安装**：把本仓库克隆或下载到 AstrBot 的 plugins 目录。

装好后在插件配置里指定秘书模型，按需打开群聊上下文接管，就能开工了。所有配置都带中文说明，在 WebUI 里看着调即可。

## 想深挖

- 机制全景：[docs/angelheart_workflow.md](docs/angelheart_workflow.md)
- 上下文注入：[docs/angelheart_context_injection_guide.md](docs/angelheart_context_injection_guide.md)
- 主动应答 API：[docs/proactive_api_guide.md](docs/proactive_api_guide.md)
- 更新记录：[CHANGELOG.md](CHANGELOG.md)
- 开发约定：[AGENTS.md](AGENTS.md)

## 贡献

欢迎提交 Issue 和 Pull Request。

## 许可证

本项目采用 GNU Affero General Public License v3 — 见 [LICENSE](LICENSE)。

## 致谢

- 感谢 [AstrBot](https://github.com/Soulter/AstrBot) 提供的平台
- 三款内置字体来自[霞鹜字体系列](https://github.com/lxgw)：[新晰黑](https://github.com/lxgw/LxgwNeoXiHei)、[新致宋](https://github.com/lxgw/LxgwNeoZhiSong) 遵循 IPA Font License 1.0，[文楷](https://github.com/lxgw/LxgwWenKai) 遵循 SIL OFL 1.1
- 感谢所有贡献者和用户的支持
