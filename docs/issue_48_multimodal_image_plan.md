# Issue 48 多模态图片上下文修复（已实施记录）

> 状态：已实施。本文保留问题背景、边界、实现要点和验收结果；当前实现见 `roles/front_desk.py`，测试见 `tests/test_issue_48_multimodal_images.py`。

## 背景

Issue 48 的现象：

- 主模型本身支持图片多模态。
- 用户没有配置 AngelHeart 自己的图片转述模型。
- 开启 AngelHeart 后，当前轮最新图片没有进入最终 LLM 请求。
- 到下一轮时，上一轮图片变成历史上下文，反而可能被模型看到。

历史根因：

- AngelHeart 重写 `req.contexts` 和 `req.prompt`。
- 当前事件对应的消息会从 `contexts` 中跳过，避免与 `req.prompt` 重复。
- 旧版 `_update_request()` 无条件执行 `req.image_urls = []`。
- 因此旧版当前事件的图片既不在 `contexts`，也不在 `req.image_urls`。

## 关键边界

这次修复只处理一个明确场景：

```text
主模型支持图片多模态
=> 当前轮图片应保留 AstrBot 原生传递链路
```

也就是说：

- 当前轮图片不由 AngelHeart 转 base64。
- 当前轮图片不塞进 `prompt`。
- Provider 支持图片时，当前轮图片沿 AstrBot 原生 `req.image_urls` 链路传递；AngelHeart 会优先使用账本中对应的缓存图片路径，避免引用已失效的临时附件路径。

不能破坏的其他场景：

- 主模型不支持图片时，不能把图片继续传给主模型。
- AngelHeart 开启自己的图片转述模型时，不应对主模型已支持的当前轮图片强制转述，避免重复处理。
- 历史上下文中的图片仍由 AngelHeart ledger/context 逻辑管理。
- `provider.modalities` 未声明或为空列表时，按 AstrBot 兼容策略视为未限制能力，不主动过滤图片或强制转述。

## 场景矩阵

### 场景 A：主模型支持图片

配置/能力：

```text
provider.modalities 包含 image
或 provider.modalities 未声明/为空
```

期望行为：

- 保留当前请求中的 `req.image_urls`。
- 不强制触发 AngelHeart 当前轮图片转述。
- `prompt` 中只保留文本和图片编号提示。
- 下游 AstrBot/provider 负责把 `req.image_urls` 转成最终多模态图片 block。

这是 issue 48 要修的核心场景。

### 场景 B：主模型不支持图片，AngelHeart 配置了图片转述

配置/能力：

```text
provider.modalities 不包含 image
image_caption_provider_id 非空
```

期望行为：

- 不把图片直传给主模型；主模型请求中的 `req.image_urls` 会被清空，历史上下文中的图片也会按 Provider 能力过滤。
- `image_caption_provider_id` 供 `angel_describe_image` 按需图片理解工具使用，不会自动为每张图片生成描述。
- `prompt` 可以保留 `[图片1]` 等文本锚点；只有主模型实际调用图片理解工具后，才会得到对应细节。

### 场景 C：主模型支持图片，AngelHeart 也配置了图片转述

配置/能力：

```text
provider.modalities 包含 image
image_caption_provider_id 非空
```

边界要求：

- 不能因为本修复导致“图片直传 + AngelHeart 转述”双重输入。
- 当前事件图片仍保留 `req.image_urls`，走 AstrBot 原生多模态链路。
- 不额外把当前事件图片塞进 prompt，也不把当前事件图片由 AngelHeart 转 base64 后传给主模型。
- AngelHeart 图片转述配置只在主模型不支持图片时用于当前轮降级；历史图片仍由 ledger/context 逻辑管理。

当前 issue 的核心规则是：主模型支持图片时，当前轮图片保留原生直传链路并使用可用的账本缓存路径；主模型不支持图片时，才清空直传图片。

### 场景 D：主模型不支持图片，AngelHeart 未配置图片转述

配置/能力：

```text
provider.modalities 不包含 image
image_caption_provider_id 为空
```

期望行为：

- 不把图片传给主模型。
- prompt 中最多保留 `[图片1]` 和图片路径/失败提示。
- 不能让纯文本模型收到 `image_url` block。

## AstrBot 原生图片链路

当前事件的图片应该走 AstrBot 原生字段：

```json
{
  "prompt": "帮我看看这张",
  "image_urls": [
    "C:/.../compressed_xxx.jpg"
  ],
  "contexts": []
}
```

下游 provider 会把 `prompt + image_urls` 组装成当前 user message：

```json
{
  "role": "user",
  "content": [
    {
      "type": "text",
      "text": "帮我看看这张"
    },
    {
      "type": "image_url",
      "image_url": {
        "url": "data:image/jpeg;base64,<由AstrBot下游转换>"
      }
    }
  ]
}
```

因此 AngelHeart 不应把当前轮图片转成 base64 后塞进 `prompt`。

## AngelHeart 历史图片链路

AngelHeart 自己需要处理的是进入 ledger 的历史消息。

一条用户消息在 ledger 中可能是：

```json
{
  "role": "user",
  "content": [
    {
      "type": "text",
      "text": "帮我看这两张"
    },
    {
      "type": "image_url",
      "image_url": {
        "url": "data:image/jpeg;base64,<IMAGE_1_BASE64>"
      },
      "original_url": "C:/tmp/a.jpg",
      "original_file_url": "C:/tmp/a.jpg"
    },
    {
      "type": "image_url",
      "image_url": {
        "url": "data:image/jpeg;base64,<IMAGE_2_BASE64>"
      },
      "original_url": "C:/tmp/b.jpg",
      "original_file_url": "C:/tmp/b.jpg"
    }
  ],
  "sender_id": "123456",
  "sender_name": "小明",
  "source_message_id": "message-uuid-1",
  "timestamp": 1780000000.0,
  "chat_id": "aiocqhttp:FriendMessage:123456"
}
```

这部分是历史上下文用的，不等同于当前事件的 `req.image_urls`。

## 多图与阻塞聚合需求

阻塞/扣押期间可能聚合多条消息：

```text
小明: 帮我看看 + 图片A
小明: 还有这张 + 图片B + 图片C
小红: 这张也对比一下 + 图片D
```

当前 prompt 文本应该能表达图片顺序：

```text
[小明 (ID: 123456)]: 帮我看看 [图片1]
[小明 (ID: 123456)]: 还有这张 [图片2] [图片3]
[小红 (ID: 456789)]: 这张也对比一下 [图片4]
```

注意：

- `[图片N]` 是文本锚点，不是 base64。
- 编号按本轮聚合顺序全局递增。
- 一条消息多图时，编号继续递增。
- 多条消息聚合时，编号不能每条消息重置。
- 扣押队列会保留最新等待事件；因此当前事件通常是本轮最新一条。此前已入账但不属于当前事件的图片，可以作为额外多模态块补到 `extra_user_content_parts`，当前事件图片继续保留在 `req.image_urls`。

## 最终请求示例

### 支持图片

用户当前轮发送：

```text
小明: 帮我看这两张 + 图片A + 图片B
```

AngelHeart 重写后应保留：

```json
{
  "prompt": "[小明 (ID: 123456)]: 帮我看这两张 [图片1] [图片2]",
  "image_urls": [
    "C:/.../compressed_a.jpg",
    "C:/.../compressed_b.jpg"
  ],
  "contexts": [
    {
      "role": "user",
      "content": "历史消息..."
    }
  ]
}
```

下游最终会组装成：

```json
{
  "role": "user",
  "content": [
    {
      "type": "text",
      "text": "[小明 (ID: 123456)]: 帮我看这两张 [图片1] [图片2]"
    },
    {
      "type": "image_url",
      "image_url": {
        "url": "data:image/jpeg;base64,<IMAGE_1_BASE64>"
      }
    },
    {
      "type": "image_url",
      "image_url": {
        "url": "data:image/jpeg;base64,<IMAGE_2_BASE64>"
      }
    }
  ]
}
```

AngelHeart 不负责当前轮这两个 base64 的生成。

### 不支持图片但开启 AngelHeart 图片转述

用户当前轮发送：

```text
小明: 帮我看这两张 + 图片A + 图片B
```

AngelHeart 重写后应类似：

```json
{
  "prompt": "[小明 (ID: 123456)]: 帮我看这两张 [图片1] [图片2]",
  "image_urls": [],
  "contexts": [
    {
      "role": "user",
      "content": "历史消息..."
    }
  ]
}
```

> 上例表示未调用 `angel_describe_image` 时的默认请求；主模型实际调用图片理解工具后，工具结果才会补充图片细节。

## 实现要点

1. 已增加 Provider 图片能力判断：`modalities` 包含 `image`，或未声明/为空时，按兼容策略保留图片。
2. 已增加当前轮 prompt 图片编号：输入 `recent_dialogue`，输出带跨消息递增 `[图片N]` 锚点的文本，不处理 base64，不修改 ledger。
3. `_update_request()` 已按 Provider 能力处理当前轮图片：
   - 不支持图片时清空 `req.image_urls`。
   - 支持图片时使用当前消息对应的图片路径，并将阻塞聚合中的非当前图片追加到 `extra_user_content_parts`。
4. 历史 contexts 继续由 `MessageProcessor` 构建；不支持图片的 Provider 会过滤历史图片。
5. 已补充支持图片、能力未声明、多图编号、聚合图片和无 base64 prompt 等边界测试。

## 验收结果

- Issue 48 场景下，当前轮最新图片会进入最终 LLM 请求。
- 当前轮图片沿 AstrBot 原生 `req.image_urls` 链路处理，不由 AngelHeart 转 base64。
- 主模型不支持图片时，不会把图片传给主模型。
- 已配置图片理解模型时，主模型可通过 `angel_describe_image` 按需获取图片细节，不强制自动转述。
- 多图和阻塞聚合时，`[图片N]` 顺序稳定且和图片输入顺序一致。
