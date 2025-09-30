# 高级多模态请求体组建指南

## 1. 引言

本指南旨在为插件开发者提供一份关于 AstrBot 框架中 LLM 请求体（`ProviderRequest`）组建流程的深度解析。理解这一流程对于实现复杂的多模态交互、精确控制上下文以及优化与大语言模型（LLM）的通信至关重要。

我们将详细阐述从原始消息事件（`AstrMessageEvent`）到最终发送给 LLM Provider（如 OpenAI）的 JSON `payload` 的完整生命周期，并重点解释 `contexts`、`prompt` 和 `image_urls` 这三个核心参数的设计理念与正确用法。

---

## 2. 核心概念：预填充与修改的设计哲学

在深入技术细节之前，必须理解 AstrBot 框架在处理 LLM 请求时的一个核心设计哲学：**上游预填充，插件后修改**。

-   **上游调度器（AstrBot Core）**: 当一个消息事件触发默认的 LLM 调用流程时，框架的调度器会作为“上游”，率先创建一个 `ProviderRequest` 对象。
-   **自动预填充 (`Pre-filling`)**: 在创建过程中，调度器会自动解析当前 `AstrMessageEvent`，并将所有相关信息**预先填充**到 `ProviderRequest` 实例中。这包括：
    -   将当前消息的文本和图片分别填入 `req.prompt` 和 `req.image_urls`。
    -   从主数据库加载历史对话，填入 `req.contexts`。
-   **插件钩子 (`@filter.on_llm_request`)**: 这是一个**拦截点**。当插件的钩子函数被调用时，它收到的 `req` 对象是一个**已经被上游完全填充好、随时可以发送给 LLM 的“完备请求体”**。
-   **插件的角色是“修改者”**: 因此，插件的核心职责不是从零开始创建请求，而是在这个完备的请求体上进行**修改、覆盖或清空**，以实现自定义的上下文逻辑。

这个设计的核心优势在于，它为插件提供了一个功能齐全的默认请求，插件开发者只需关注自己需要修改的部分，而无需处理繁琐的请求组装细节。

---

## 3. 核心参数的数据结构与设计意图

`ProviderRequest` 对象（在代码中通常是 `req`）包含三个关键参数，它们共同定义了 LLM 的输入。

### 3.1 `req.contexts: list[dict]`

-   **数据结构**: 一个标准的、符合 OpenAI `messages` 格式的字典列表。每个字典代表一条历史消息。
-   **设计意图**: **专门用于承载历史对话上下文**。
-   **关键行为**:
    -   **上游自动填充**: 在插件钩子被调用前，AstrBot 框架已从其主数据库中加载最近的对话历史，并填充好此字段。
    -   **插件可覆盖**: 插件可以通过 `req.contexts = my_custom_history` 的方式，用自己管理的、更精确的历史记录（例如，从插件自身的 `ConversationLedger` 构建）来**完全覆盖**上游填充的内容。这是解决原生历史上下文混乱或不符合预期的核心手段。
    -   `content` 字段支持多模态，可以直接包含符合 OpenAI 格式的 `image_url` 对象，让 LLM “看到”历史图片。

### 3.2 `req.prompt: str`

-   **数据结构**: 一个纯字符串 (`str`)。
-   **设计意图**: **专门用于承载当前的核心用户输入或插件生成的指令**。它定义了 LLM 在看到所有历史之后，“现在”需要做什么。
-   **关键行为**:
    -   它不应被用来拼接冗长的历史记录。
    -   在框架的组装流程中，它会与 `image_urls` 结合，形成一个全新的、`role: "user"` 的消息记录，并被追加到 `contexts` 列表的末尾。

### 3.3 `req.image_urls: list[str]`

-   **数据结构**: 一个字符串列表 (`list[str]`)，其中每个字符串都是一个有效的图片标识符（HTTP URL、本地文件路径或 `base64://` URI）。
-   **设计意图**: **专门用于承载与 `prompt` 文本紧密相关的、属于“当前消息”的图片**。
-   **关键行为**:
    -   **上游自动填充**: 在插件钩子被调用前，框架已从当前 `event` 中解析出图片，并将其 URL 填充到此列表中。
    -   **插件可覆盖**: 插件可以自由地修改此列表，例如通过 `req.image_urls = []` 将其清空（当图片信息已在 `contexts` 中处理时），或替换为其他图片 `req.image_urls = ["http://new-image.com/a.jpg"]`。
    -   在最终的组装流程中，它会与 `prompt` 一起被转换成一个多模态的 `content` 数组。

---

## 4. 请求体的组建流程：从预填充到最终组装

理解了参数的设计意图后，我们来追踪数据从上游创建到最终生成 JSON `payload` 的完整旅程。

#### 阶段 1：上游预填充 `ProviderRequest`

-   在插件的 `@filter.on_llm_request` 钩子被触发**之前**，AstrBot 框架的核心调度器已经创建了一个 `ProviderRequest` 对象（`req`），并完成了预填充。
-   此时的 `req` 对象已经是一个“完备”的请求体：
    -   `req.contexts` 已包含从主数据库加载的历史对话。
    -   `req.prompt` 和 `req.image_urls` 已包含当前消息的文本和图片。

#### 阶段 2：插件介入与修改

-   插件的钩子函数被调用，接收到这个**已经填充好的 `req` 对象**。
-   插件根据自身逻辑，对 `req` 进行**修改**。例如，执行“最佳实践”中的操作：
    ```python
    # 在钩子函数内部
    req.contexts = build_my_custom_history() # 用插件的精确历史覆盖原生历史
    req.prompt = "这是我的指令"               # 用纯指令覆盖当前消息文本
    req.image_urls = []                      # 清空当前图片，因其已在 contexts 中处理
    ```

#### 阶段 3：Provider 最终组装 (`_prepare_chat_payload`)

-   修改后的 `req` 对象继续在框架中传递，最终到达具体的 Provider（如 `ProviderOpenAIOfficial`）。
-   Provider 的 `_prepare_chat_payload` 方法开始执行最终的组装。
-   **组装“当前消息” (`new_record`)**：
    -   它处理**修改后**的 `req.prompt` 和 `req.image_urls`。在我们的最佳实践下，`image_urls` 为空，所以 `new_record` 是一个简单的纯文本消息：
        ```json
        { "role": "user", "content": "这是我的指令" }
        ```
-   **拼接“历史”与“当前”**：
    -   方法执行 `[*req.contexts, new_record]`，将插件**覆盖后**的历史 (`req.contexts`) 与刚刚组装的当前指令消息 (`new_record`) 拼接起来。
-   **生成最终 `payload`**:
    -   这个拼接好的列表被赋值给最终 JSON `payload` 的 `messages` 字段，发送给 LLM API。

---

## 5. 最佳实践与推荐方案

基于对“预填充与修改”模型的理解，解决“上下文混乱”和“图片处理不精确”问题的最佳实践如下：

1.  **完全控制 `contexts`**：
    -   在 `@filter.on_llm_request` 钩子中，用插件自己管理的、结构精确的历史记录**覆盖** `req.contexts`。
    -   这意味着你需要一个函数，能将你自己的 `ConversationLedger` 中的数据，转换为符合 OpenAI `messages` 格式的字典列表。

2.  **分离指令与数据**：
    -   将当前用户的输入（包括文本和图片）视为历史的一部分，并将其处理进 `req.contexts` 的最后一条记录中。
    -   用一个**纯粹的、不包含用户输入的指令性文本**覆盖 `req.prompt`，例如：“根据以上对话历史进行总结”。
    -   **清空** `req.image_urls` (`req.image_urls = []`)，因为所有图片都应在 `req.contexts` 中被统一管理。

**示例代码片段（在钩子函数中）：**
```python
# req 是已经由上游填充好的 ProviderRequest 对象

# 1. 从插件 Ledger 构建包含完整历史（含当前消息图片）的 new_contexts
#    这是你需要实现的核心逻辑
new_contexts = build_my_structured_history_from_ledger()

# 2. 完全覆盖原生 contexts
req.contexts = new_contexts

# 3. 注入纯指令到 prompt
req.prompt = "任务指令：请根据以上完整的对话历史，分析用户的情绪并给出回应。"

# 4. 确保 image_urls 为空，因为所有图片都已在 new_contexts 中处理
req.image_urls = []
```

遵循这一模式，您可以构建出既能精确控制历史上下文（包括多模态内容），又能充分利用 AstrBot 框架的“预填充”便利性，从而实现健壮且可维护的插件。