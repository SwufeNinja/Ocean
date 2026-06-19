# LLM 对话后端实施计划

## 背景

当前后端已经暴露了基础 LLM 对话接口，但会话状态保存在 `make_app` 内部的内存字典中。这样会导致服务重启后历史记录丢失，而且现有会话只按账号和知识库隔离，没有记录“这个对话使用了哪些知识库文档作为上下文”。

目标行为：

- 用户可以不选择文档，直接与 LLM 多轮对话。
- 用户可以选择最多 5 篇知识库文档作为上下文，与 LLM 多轮对话。
- 对话历史需要持久化保存，用户下次打开系统仍能看到。
- 当前端打开文档 A 时，右侧对话面板可以列出所有上下文文档集合中包含文档 A 的历史对话。

## 目标

1. 将 LLM 会话从进程内存存储迁移到持久化存储。
2. 为每个会话增加明确的上下文文档元数据。
3. 支持按账号、知识库、上下文包含的文档查询会话。
4. 发送消息时，后端根据会话历史和上下文文档组装 LLM prompt。
5. 尽量保留现有 `/api/llm/conversations` API 形态，降低前端迁移成本。

## 非目标

- 第一阶段不实现向量检索。
- 第一阶段不自动摘要或改写知识库文档。
- 单个会话不支持超过 5 篇上下文文档。
- 即使上下文使用公共文档，对话仍归属于发起用户账号，不跨账号共享。

## 数据模型

新增持久化 LLM 会话和消息记录。

推荐 Elasticsearch 索引：

```text
ocean_llm_conversations_v1
ocean_llm_messages_v1
```

`ocean_llm_conversations_v1` 建议字段：

```json
{
  "conversation_id": "abc123",
  "account_id": "local",
  "knowledge_base_id": "default",
  "title": "青年政策分析",
  "context_mode": "documents",
  "context_document_ids": ["doc_a", "doc_b"],
  "context_documents": [
    {
      "document_id": "doc_a",
      "file_name": "a.pdf",
      "title": "a.pdf",
      "account_id": "local",
      "knowledge_base_id": "default",
      "snapshot_version": "optional",
      "order": 0
    }
  ],
  "system_prompt": "Answer in Chinese",
  "provider": "openai_compatible",
  "model": "model-name",
  "temperature": 0,
  "max_tokens": 4096,
  "message_count": 4,
  "status": "active",
  "created_at": "2026-06-18T10:00:00+08:00",
  "updated_at": "2026-06-18T10:05:00+08:00",
  "deleted_at": null
}
```

`ocean_llm_messages_v1` 建议字段：

```json
{
  "message_id": "msg123",
  "conversation_id": "abc123",
  "account_id": "local",
  "knowledge_base_id": "default",
  "role": "user",
  "content": "请概括这几篇文档",
  "sequence": 1,
  "created_at": "2026-06-18T10:01:00+08:00",
  "metadata": {}
}
```

索引要求：

- `context_document_ids` 使用 keyword 数组，便于文档侧栏用 `term` 过滤。
- `account_id`、`knowledge_base_id`、`conversation_id`、`status`、`updated_at` 支持过滤和排序。
- 消息按 `conversation_id` 查询，并按 `sequence` 升序返回。

## API 设计

保留现有路由，扩展请求和响应字段。

### 创建会话

`POST /api/llm/conversations`

请求示例：

```json
{
  "title": "可选标题",
  "knowledge_base_id": "default",
  "context_documents": [
    {"document_id": "doc_a"},
    {"document_id": "doc_b"}
  ],
  "system_prompt": "可选系统提示词"
}
```

规则：

- `context_documents` 可选。
- 不传或传空数组时，创建通用对话。
- 如果传入文档，数量必须是 1 到 5 篇，且 document_id 不得重复。
- 每篇文档必须存在于目标知识库，并且当前用户可访问。
- 公共文档可以作为上下文，但会话的 `account_id` 仍然是当前用户。

### 查询会话列表

`GET /api/llm/conversations`

支持查询参数：

```text
knowledge_base_id=default
document_id=doc_a
context_mode=none|documents
limit=100
```

行为：

- 不传 `document_id` 时，返回当前用户在该知识库下的会话。
- 传入 `document_id` 时，只返回 `context_document_ids` 包含该文档的会话。
- 通用对话不会匹配文档侧栏查询。
- 默认按 `updated_at desc` 排序。

### 获取单个会话

`GET /api/llm/conversations/{conversation_id}`

返回会话元数据和按 `sequence` 升序排列的消息列表。

### 发送消息

`POST /api/llm/conversations/{conversation_id}/messages`

请求示例：

```json
{
  "content": "用户消息",
  "options": {
    "temperature": 0.1
  }
}
```

行为：

- 读取持久化会话元数据和消息历史。
- 重新校验当前用户是否有权访问该会话。
- 如果会话绑定了上下文文档，从知识库读取仍可访问的文档 Markdown/OCR 文本。
- 组装 LLM prompt：系统提示词、受限长度的文档上下文、历史消息、当前用户消息。
- LLM 调用成功后，持久化用户消息和助手消息。
- 更新会话标题、`message_count` 和 `updated_at`。

### 删除会话

`DELETE /api/llm/conversations/{conversation_id}`

优先软删除：

```json
{
  "status": "deleted",
  "deleted_at": "..."
}
```

列表接口默认不返回已删除会话。

## 上下文 Prompt 策略

第一阶段采用确定性的文档内容注入，不引入向量检索。

1. 从现有知识库文档存储读取每篇上下文文档的 Markdown 或分页文本。
2. 按清晰边界组织上下文：

```text
[Document 1]
document_id: doc_a
file_name: a.pdf

Page 1:
...

Page 2:
...
```

3. 调用 LLM 前应用可配置的上下文长度预算。
4. 截断时优先保留页边界或 chunk 边界，避免在句子中间任意切断。
5. prompt 中要求模型在使用文档内容回答时尽量引用文档名和页码。

建议配置：

```yaml
llm:
  conversation:
    max_context_documents: 5
    max_document_context_chars: 30000
    max_history_messages: 20
```

## 实施阶段

### 阶段 1：存储抽象

- 将 `ChatMessage` 和 `LlmConversation` 从 `web.py` 内部临时结构迁移为稳定模型，或统一使用明确的 dict schema。
- 增加 LLM 会话存储抽象，至少包含：
  - `ensure_indices()`
  - `create_conversation(...)`
  - `list_conversations(...)`
  - `get_conversation(...)`
  - `append_messages(...)`
  - `soft_delete_conversation(...)`
- 在 `src/ocean/storage/elasticsearch.py` 中实现 Elasticsearch 存储。
- 保留测试或 Elasticsearch 未启用时可用的内存 fallback，但明确它不是持久化方案。

### 阶段 2：上下文文档校验

- 扩展创建会话接口，支持 `context_documents`。
- 校验重复 ID，并限制最多 5 篇文档。
- 通过现有 document store 校验文档存在性、账号可见性和知识库范围。
- 在会话记录中保存轻量上下文快照。

### 阶段 3：列表分类查询

- 扩展 `GET /api/llm/conversations`，支持 `document_id` 和 `context_mode`。
- 实现 `context_document_ids contains document_id` 查询。
- 增加测试：绑定文档 A、B 的会话应同时出现在 A 和 B 的文档侧栏列表中。
- 增加测试：通用对话不出现在文档侧栏列表中。

### 阶段 4：Prompt 组装

- 增加 helper 统一组装 LLM messages：
  - 配置级或会话级 system prompt。
  - 上下文文档块。
  - 最近若干轮持久化消息。
  - 当前用户消息。
- 按页或 chunk 边界做确定性截断。
- 上下文预算和历史消息数量通过配置控制。
- 增加测试，断言 LLM 请求中包含文档名、document_id、页码标记和历史消息。

### 阶段 5：消息持久化

- 用户消息和助手消息按递增 `sequence` 写入。
- LLM 调用失败时不写入不完整的助手消息。
- 成功响应后更新会话标题、`message_count` 和 `updated_at`。
- 增加服务重启等价测试：用同一个 store 重新创建 app 后，仍可查询会话和消息。

### 阶段 6：前端对接说明

- 现有前端可以继续使用 `/api/llm/conversations` 和 `/messages`。
- 文档详情页加载侧栏历史时调用 `GET /api/llm/conversations?document_id=<current_document_id>`。
- 通用聊天页不传 `document_id`。
- 从文档页新建会话时，把当前文档放入 `context_documents`。
- 从多文档选择 UI 新建会话时，把选中的文档 ID 放入 `context_documents`，最多 5 篇。

## 测试计划

后端自动化测试：

- 创建并继续通用对话。
- 创建单文档上下文对话。
- 创建五文档上下文对话。
- 拒绝六篇上下文文档。
- 拒绝重复上下文文档。
- 拒绝不存在或无权访问的文档。
- 按 `document_id` 查询，验证“上下文包含该文档”的分类语义。
- 验证会话和消息可跨 app/store 重新实例化保留。
- 验证 LLM prompt 包含文档上下文和历史消息。
- 验证 LLM 调用失败时不会追加助手消息。
- 验证软删除会话不会出现在列表中。

手工验证：

- 用户 A 基于私有文档和公共文档分别创建对话。
- 用户 B 不能看到用户 A 的对话。
- 打开文档 A，确认所有上下文包含 A 的会话都会出现在右侧面板。
- 重启后端，确认历史对话仍可见。

## 待确认问题

1. 上下文快照是否需要保存完整 Markdown，还是只保存文档元数据并在发送新消息时实时读取文档？
2. 源文档被删除或重新 OCR 后，旧会话是否继续允许发送新消息，还是改为只读？
3. 长文档上下文第一版用简单截断，还是直接引入关键词/向量检索召回？
4. 是否需要在消息记录中保存 token 用量和 provider request id，用于后续成本统计？
