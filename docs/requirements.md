# PDF OCR 与政策文本提取系统需求文档（初稿）

## 1. 项目背景

本项目面向学术研究场景，主要处理批量 PDF 文献、档案、报刊、书籍或扫描资料。用户希望先通过 OCR 将 PDF 转换为可检索、可分析的文本，再基于关键词或大模型语义理解提取与特定主题相关的章节、段落或文章内容。

核心使用场景包括：

- 批量处理十余个或更多 PDF 文件。
- 识别扫描版、繁体中文、竖排版、混合版式等复杂 PDF。
- 将 OCR 结果转换为 Markdown 或结构化文本。
- 保留 OCR 文本与原始 PDF 页码之间的对应关系，便于学术脚注引用。
- 从 OCR 文本中提取包含指定关键词的内容，例如包含“青年”的段落。
- 使用 LLM API 进行语义理解，提取不一定包含固定关键词、但与“党的青年工作政策”等主题相关的文本。

## 2. 项目目标

### 2.1 总体目标

构建一个面向批量 PDF 的 OCR 与智能文本提取工具链，支持：

1. 批量上传或指定多个 PDF 文件。
2. 调用 OCR 服务将 PDF 转换为 Markdown/文本。
3. 保留每段 OCR 文本对应的 PDF 页码。
4. 支持关键词检索与段落提取。
5. 支持基于 LLM 的主题相关内容识别与提取。
6. 输出带有来源文件名、页码、章节/段落信息的结果文件。

### 2.2 当前阶段目标

当前阶段先实现可验证的 MVP，重点解决：

- OCR 服务接入能力。
- 页码保留能力。
- Markdown/结构化文本输出能力。
- 基于关键词的段落提取能力。
- LLM API client 抽象能力，便于后续替换不同模型。
- 基于 LLM 的主题相关内容提取原型。

## 3. 用户角色

### 3.1 研究者/使用者

主要关注：

- 能否批量处理 PDF。
- OCR 是否准确。
- 是否能知道每段文本来自原 PDF 的第几页。
- 是否能快速提取与研究主题相关的材料。
- 输出结果是否方便复制到论文、笔记或数据库中。

### 3.2 项目开发者

主要关注：

- OCR 服务可切换。
- LLM 服务可切换。
- 处理流程可复用、可扩展。
- 后续可以接入不同 OCR API、不同 LLM API、不同导出格式。

## 4. 需求一：批量 PDF OCR

### 4.1 功能描述

系统需要支持一次处理多个 PDF 文件。每次批处理数量至少应支持十余个 PDF，后续可根据性能和 API 限额扩展。

OCR 服务初期考虑接入：

- PaddleOCR API。
- MinerU API。

两种 OCR 服务都需要进行效果测试，最终可根据不同 PDF 类型选择更合适的方案。

### 4.2 OCR 输入

系统应支持以下输入方式：

- 指定一个包含 PDF 的目录。
- 指定多个 PDF 文件路径。
- 后续可扩展为 Web 页面上传。

每个 PDF 文件需要记录：

- 文件名。
- 文件路径。
- 文件唯一 ID。
- 页数。
- OCR 状态。
- 使用的 OCR 服务。
- 处理开始时间与结束时间。

### 4.3 OCR 输出

OCR 后的结果至少需要输出以下内容：

- 原始 OCR 文本。
- Markdown 格式文本。
- 按页组织的结构化文本。
- 每个文本块对应的 PDF 页码。
- OCR 服务返回的置信度信息（如果 API 支持）。
- OCR 使用的模型或参数信息（如果 API 支持）。

建议输出格式包括：

1. 单个 PDF 对应一个 Markdown 文件。
2. 单个 PDF 对应一个 JSON 文件，用于保存结构化信息。
3. 批量任务对应一个总索引文件，用于记录所有文件处理结果。

示例 Markdown 输出：

```markdown
# example.pdf

<!-- source_file: example.pdf -->

## Page 1

本页 OCR 文本内容……

## Page 2

本页 OCR 文本内容……
```

示例 JSON 输出：

```json
{
  "source_file": "example.pdf",
  "ocr_engine": "paddleocr",
  "pages": [
    {
      "page_number": 1,
      "text": "本页 OCR 文本内容……",
      "blocks": [
        {
          "block_id": "p1_b1",
          "text": "段落文本……",
          "page_number": 1,
          "confidence": 0.98
        }
      ]
    }
  ]
}
```

### 4.4 页码保留要求

页码是本项目的核心要求之一。

系统必须保证：

- OCR 后的文本可以追溯到原 PDF 页码。
- 后续关键词提取和 LLM 提取的结果也必须保留页码。
- 如果提取内容跨页，应记录起止页码。
- 导出结果中必须包含来源文件名和页码。

示例提取结果：

```markdown
## 提取结果 A

- 来源文件：example.pdf
- 页码：第 12 页
- 提取方式：关键词匹配
- 命中关键词：青年

原文：
这里是包含“青年”的段落……
```

跨页示例：

```markdown
## 提取结果 B

- 来源文件：example.pdf
- 页码：第 12-13 页
- 提取方式：LLM 语义识别
- 主题：党的青年工作政策

原文：
这里是跨页的相关内容……
```

### 4.5 繁体中文、竖版与复杂版式支持

部分 PDF 可能存在以下情况：

- 繁体中文。
- 竖排版文字。
- 扫描版图片 PDF。
- 报刊多栏排版。
- 页眉、页脚、脚注、页码混杂。
- 图片、表格与正文混排。

系统需要支持通过配置切换 OCR 策略，例如：

- OCR 引擎：PaddleOCR / MinerU。
- 文字方向：自动检测 / 横排优先 / 竖排优先。
- 语言类型：简体中文 / 繁体中文 / 中英混合。
- 版面分析：开启 / 关闭。
- 表格识别：开启 / 关闭。
- Markdown 输出：开启 / 关闭。

具体 OCR 能力以实际 API 测试结果为准。系统设计上需要保留切换 OCR 引擎和参数的能力。

### 4.6 OCR 任务状态

批量 OCR 任务应支持状态记录：

- pending：等待处理。
- processing：处理中。
- success：处理成功。
- failed：处理失败。
- partial_success：部分页面成功，部分页面失败。

失败时需要记录：

- 失败文件。
- 失败页码。
- 失败原因。
- OCR 服务返回的错误信息。
- 是否可重试。

## 5. 需求二：基于 OCR 文本的关键词与语义提取

### 5.1 功能描述

在 OCR 文本完成后，系统需要从文本中提取特定主题相关内容。提取方式分为两类：

1. 关键词提取：基于固定关键词进行匹配，例如提取包含“青年”的段落。
2. 语义提取：基于 LLM API 进行理解，提取与某类政策主题相关的文本，即使文本中没有出现固定关键词。

### 5.2 关键词提取

系统应支持用户配置关键词，例如：

- 青年。
- 共青团。
- 青年工作。
- 青年政策。
- 青年干部。

关键词提取需要支持：

- 单关键词。
- 多关键词。
- 任意关键词命中。
- 全部关键词命中。
- 正则表达式匹配。
- 是否区分大小写。
- 是否进行简繁转换后匹配。

提取粒度需要支持配置：

- 命中句子。
- 命中段落。
- 命中段落及其前后 N 段。
- 命中页。
- 命中章节。
- 命中文章。

初期建议优先实现：

- 命中段落。
- 命中段落前后各 1 段。
- 命中页。

### 5.3 LLM 语义提取

除关键词提取外，系统需要支持调用 LLM API，对 OCR 文本进行语义判断。例如：

> 请识别文本中涉及“党的青年工作政策”的内容，并提取相关原文。

LLM 提取不是简单匹配关键词，而是让模型判断文本是否与主题相关。

系统需要支持：

- 配置分析主题。
- 配置提取标准。
- 配置输出粒度。
- 配置是否只输出原文。
- 配置是否输出判断理由。
- 配置是否输出置信度或相关性等级。

示例主题：

```text
党的青年工作政策，包括但不限于青年教育、青年组织、青年动员、青年干部培养、共青团工作、青年就业、青年思想政治工作、青年参与国家建设等内容。
```

示例 LLM 输出结构：

```json
{
  "items": [
    {
      "source_file": "example.pdf",
      "page_start": 12,
      "page_end": 13,
      "title_or_section": "第三章 青年工作",
      "relevance": "high",
      "reason": "该段讨论青年组织建设与政策动员，符合党的青年工作政策主题。",
      "text": "原文内容……"
    }
  ]
}
```

### 5.4 LLM API Client 要求

系统需要实现一个可替换的 LLM API client。当前不绑定具体供应商，只需要支持配置 API 即可。

Client 应支持配置：

- API base URL。
- API key。
- model 名称。
- temperature。
- max tokens。
- timeout。
- retry 次数。
- system prompt。
- user prompt 模板。

建议通过环境变量或配置文件管理：

```env
LLM_API_BASE_URL=https://api.example.com/v1
LLM_API_KEY=your_api_key
LLM_MODEL=your_model_name
LLM_TEMPERATURE=0
LLM_MAX_TOKENS=4096
```

Client 应避免把具体模型写死，后续可接入：

- DeepSeek API。
- OpenAI API。
- 其他兼容 OpenAI Chat Completions 格式的 API。
- 本地部署模型 API。

### 5.5 文本切分与上下文窗口

由于 OCR 后的 PDF 文本可能很长，LLM 分析需要分块处理。

系统应支持按以下方式切分文本：

- 按页切分。
- 按章节切分。
- 按段落切分。
- 固定 token 长度切分。
- 滑动窗口切分，避免跨页或跨段落内容被截断。

每个文本块必须保留：

- source_file。
- page_start。
- page_end。
- chunk_id。
- chunk_index。
- text。

建议初期采用：

- 先按页组织文本。
- 再合并相邻页形成 chunk。
- 每个 chunk 保留起止页码。

### 5.6 提取结果去重与合并

关键词提取和 LLM 提取可能产生重复内容。系统需要支持结果去重与合并。

去重依据可以包括：

- source_file。
- page_start/page_end。
- 文本相似度。
- 段落 ID。
- chunk ID。

合并后的结果需要保留：

- 多种命中方式，例如关键词命中 + LLM 命中。
- 命中的关键词列表。
- LLM 判断理由。
- 页码。
- 原文。

### 5.7 LLM 多轮对话与知识库上下文

除批处理式 LLM 语义提取外，Web 后端需要支持面向用户交互的 LLM 多轮对话。对话能力应覆盖三种使用方式：

1. 通用对话：用户可以直接与 LLM 多轮对话，不需要指定任何知识库文档作为上下文。
2. 指定文档上下文对话：用户可以在发起对话时从知识库内选择文档作为上下文；单个对话最多可指定 5 篇文档。
3. 文章内对话：用户打开某篇文档后，可以在该文档界面右侧继续与 LLM 对话；该界面展示的历史对话应包括所有上下文文档集合中包含当前文档的对话。

对话记录必须持久化保存，不能只保存在进程内存中。用户下次打开系统后，应能看到之前的会话列表和消息历史，行为类似主流 LLM 应用。

每个 LLM 对话至少需要记录：

- conversation_id。
- account_id。
- knowledge_base_id。
- title。
- context_documents：本轮对话绑定的知识库文档列表，包含 document_id、file_name/title、上下文排序和必要的快照信息。
- context_mode：none / documents。
- messages：用户消息与助手消息。
- system_prompt。
- model、provider、temperature、max_tokens 等调用配置快照。
- created_at、updated_at、deleted_at 或 status。

上下文分类与查询规则：

- 通用对话的 `context_documents` 为空。
- 指定文档上下文对话的 `context_documents` 长度为 1 到 5。
- 按文档打开对话侧栏时，后端应支持按 `document_id` 查询历史对话，返回所有 `context_documents` 中包含该文档的会话。
- 如果一个对话同时使用了文档 A、B、C 作为上下文，则在文档 A、B、C 的对话侧栏中都应能看到该对话。
- 会话列表默认按 `updated_at` 倒序。
- 同账号内只能访问自己的私有对话；公共文档可作为上下文，但由用户发起的对话仍归属于该用户账号。

LLM 调用上下文组装要求：

- 未指定文档时，只发送系统提示词、历史消息和当前用户消息。
- 指定文档时，后端需要从知识库读取对应 OCR/Markdown 内容，并将文档内容以明确边界注入到 LLM prompt 中。
- 多文档上下文必须保留来源文档名、document_id 和页码信息，便于模型回答时引用来源。
- 后端应限制单次上下文注入长度，长文档需要按页、chunk 或检索结果截断，避免超过模型上下文窗口。
- 用户无权访问的文档不得被加入上下文。

## 6. 输出要求

### 6.1 OCR 输出

每个 PDF 至少输出：

- `ocr/{pdf_name}.md`：Markdown 文本。
- `ocr/{pdf_name}.json`：结构化 OCR 数据。

### 6.2 提取输出

每次提取任务至少输出：

- `extract/{task_name}.md`：适合人工阅读的提取结果。
- `extract/{task_name}.json`：适合程序读取的结构化结果。
- `extract/{task_name}.csv`：适合表格查看的结果。

### 6.3 提取结果字段

提取结果至少包含：

- result_id。
- source_file。
- page_start。
- page_end。
- extraction_method：keyword / llm / mixed。
- matched_keywords。
- topic。
- relevance。
- reason。
- text。
- created_at。

示例 CSV 字段：

```csv
result_id,source_file,page_start,page_end,method,keywords,topic,relevance,text
A001,example.pdf,12,13,llm,,党的青年工作政策,high,原文内容……
```

## 7. 配置要求

系统应通过配置文件控制 OCR 与提取流程。

示例配置：

```yaml
ocr:
  engine: paddleocr
  fallback_engine: mineru
  language: auto
  text_direction: auto
  output_markdown: true
  output_json: true

extraction:
  keywords:
    - 青年
    - 共青团
  keyword_match_mode: any
  context_before_paragraphs: 1
  context_after_paragraphs: 1
  semantic_topics:
    - name: 党的青年工作政策
      description: >
        包括青年教育、青年组织、青年动员、青年干部培养、共青团工作、
        青年就业、青年思想政治工作、青年参与国家建设等内容。
  output_formats:
    - md
    - json
    - csv

llm:
  provider: openai_compatible
  api_base_url: ${LLM_API_BASE_URL}
  api_key: ${LLM_API_KEY}
  model: ${LLM_MODEL}
  temperature: 0
  max_tokens: 4096
  timeout_seconds: 120
  retry: 3
```

## 8. 系统流程

### 8.1 OCR 流程

1. 用户指定 PDF 文件或目录。
2. 系统扫描 PDF 文件列表。
3. 系统创建批处理任务。
4. 对每个 PDF 调用 OCR API。
5. 系统保存 OCR 原始结果。
6. 系统转换为 Markdown 和结构化 JSON。
7. 系统记录每段文本对应页码。
8. 系统输出 OCR 任务报告。

### 8.2 提取流程

1. 用户选择 OCR 结果。
2. 用户配置关键词或语义主题。
3. 系统对 OCR 文本进行切分。
4. 系统执行关键词提取。
5. 系统调用 LLM API 执行语义提取。
6. 系统合并、去重、排序结果。
7. 系统导出 Markdown、JSON、CSV。
8. 用户审查提取结果。

## 9. 技术设计建议

### 9.1 模块划分

建议系统拆分为以下模块：

- `pdf_loader`：扫描和读取 PDF 文件信息。
- `ocr_client`：OCR API 抽象层。
- `paddleocr_client`：PaddleOCR API 实现。
- `mineru_client`：MinerU API 实现。
- `ocr_normalizer`：统一不同 OCR 服务的返回格式。
- `markdown_exporter`：导出 Markdown。
- `text_chunker`：文本切分。
- `keyword_extractor`：关键词提取。
- `llm_client`：LLM API 抽象层。
- `semantic_extractor`：基于 LLM 的语义提取。
- `result_merger`：去重与合并。
- `exporter`：导出 JSON/Markdown/CSV。

### 9.2 OCR Client 抽象

OCR client 应提供统一接口：

```python
class OcrClient:
    def recognize_pdf(self, pdf_path: str, options: dict) -> dict:
        """Return normalized OCR result with page-level text."""
        raise NotImplementedError
```

统一返回格式应尽量与具体 OCR 服务解耦。

### 9.3 LLM Client 抽象

LLM client 应提供统一接口：

```python
class LlmClient:
    def chat(self, messages: list[dict], options: dict | None = None) -> str:
        """Call a configured LLM API and return text response."""
        raise NotImplementedError
```

优先支持 OpenAI-compatible API，方便接入 DeepSeek、OpenAI 或其他兼容服务。

## 10. MVP 范围

第一版建议只做以下能力：

1. 从本地目录批量读取 PDF。
2. 接入一个 OCR API，并保留 OCR client 抽象。
3. 输出按页组织的 Markdown 和 JSON。
4. 实现关键词段落提取。
5. 实现一个 OpenAI-compatible LLM client。
6. 实现基于 LLM 的主题相关内容提取原型。
7. 输出提取结果为 Markdown 和 JSON。

暂不优先实现：

- Web 页面上传。
- 用户账号系统。
- 数据库管理后台。
- 高级可视化。
- 人工标注界面。
- 多人协作。

## 11. 验收标准

### 11.1 OCR 验收标准

- 可以一次处理至少 10 个 PDF 文件。
- 每个 PDF 都能生成 Markdown 和 JSON 文件。
- Markdown 中可以明确看到页码分隔。
- JSON 中每个 page/chunk/block 都有 page_number 或 page_start/page_end。
- OCR 失败时有错误记录，不影响其他文件继续处理。
- 可以通过配置切换 OCR 引擎或 OCR 参数。

### 11.2 关键词提取验收标准

- 可以配置一个或多个关键词。
- 可以提取包含关键词的段落。
- 提取结果包含来源文件名和页码。
- 可以导出 Markdown 和 JSON。
- 可以配置命中段落前后上下文范围。

### 11.3 LLM 语义提取验收标准

- 可以配置 LLM API base URL、API key 和 model。
- 可以配置分析主题。
- 可以对 OCR 文本分块调用 LLM。
- LLM 输出结果包含原文、页码、来源文件和相关性判断。
- 结果可以导出为 Markdown 和 JSON。
- 单个 chunk 调用失败时可以重试或记录失败，不中断整个批处理。

### 11.4 LLM 多轮对话验收标准

- 用户可以创建不绑定文档的通用 LLM 多轮对话。
- 用户可以创建绑定 1 到 5 篇知识库文档的 LLM 多轮对话。
- 创建绑定文档的对话时，后端会校验文档存在且当前用户有访问权限。
- 发送消息时，LLM 请求包含会话历史和已绑定文档上下文。
- 对话和消息会持久化保存；服务重启后仍可查询历史会话和消息。
- 会话列表支持按账号、知识库和上下文文档过滤。
- 打开文档 A 时，后端可以返回所有上下文包含文档 A 的历史对话。
- 通用对话不会出现在任意文档的文章内对话列表中，除非后续显式绑定文档。
- 删除会话应优先使用软删除，避免误删审计数据。

## 12. 风险与待验证问题

### 12.1 OCR 效果风险

不同 PDF 的 OCR 效果差异可能很大，尤其是：

- 低清扫描件。
- 竖版繁体。
- 报刊多栏。
- 表格和正文混排。
- 页眉页脚干扰。

需要通过 PaddleOCR 与 MinerU 分别测试样本 PDF，再决定默认 OCR 方案。

### 12.2 页码准确性风险

如果 OCR API 返回的结构不稳定，可能导致文本块与页码对应不精确。系统必须优先选择能够保留页级信息的 OCR 返回格式。

### 12.3 LLM 幻觉风险

LLM 可能会总结、改写或误判内容。由于本项目涉及学术引用，系统应要求 LLM：

- 尽量只提取原文。
- 不自行改写原文。
- 判断理由与原文分开。
- 每条结果必须绑定页码和来源。

### 12.4 API 成本与限额

OCR API 和 LLM API 都可能存在调用限额、并发限制和费用问题。系统需要记录：

- 处理页数。
- OCR 调用次数。
- LLM 调用次数。
- token 消耗。
- 失败和重试次数。

具体免费额度、价格和限制需要以后按实际服务商文档确认。

### 12.5 LLM 对话持久化与上下文风险

- 长文档或多文档上下文可能超过模型上下文窗口，需要明确截断、摘要或检索策略。
- 对话历史和文档上下文同时注入时，token 成本可能明显上升，需要记录调用参数和用量。
- 会话按“上下文包含某文档”分类时，同一会话会出现在多个文档侧栏中，前端需要避免让用户误解为多份独立副本。
- 文档被删除、重 OCR 或权限变化后，历史会话需要保留上下文快照，同时在新消息发送前重新校验可访问性。

## 13. 待确认问题

1. 项目最终是命令行工具、桌面工具，还是 Web 工具？
2. 是否需要数据库保存历史任务，还是文件夹输出即可？
3. OCR 结果是否需要人工校对界面？
4. 提取结果是否需要人工确认后再导出？
5. 是否需要支持 Word/Excel 导出？
6. 是否需要支持简繁转换后的关键词匹配？
7. LLM 输出是否必须只保留原文，还是允许附带摘要和理由？
8. 对脚注引用而言，是否只需要页码，还是也需要 PDF 文件名、章节名、段落编号？
9. 是否需要保留 OCR 坐标信息，以便未来跳转或高亮原 PDF 位置？
10. 是否有一批典型测试 PDF，可用于比较 PaddleOCR 与 MinerU 效果？
11. LLM 对话上下文超过模型窗口时，优先采用固定截断、全文摘要，还是基于检索的相关片段召回？
12. 历史对话是否需要支持重命名、归档、收藏和导出？
13. 删除知识库文档后，包含该文档的历史对话是否继续可见，还是仅在全局对话列表中保留？

## 14. 建议开发顺序

1. 建立项目基础结构与配置文件。
2. 实现 OCR client 抽象。
3. 接入 PaddleOCR 或 MinerU 中的一个服务。
4. 设计统一 OCR JSON 数据结构。
5. 实现 Markdown 导出，并保留页码。
6. 实现关键词提取。
7. 实现 OpenAI-compatible LLM client。
8. 实现 LLM 语义提取。
9. 实现结果合并、去重与导出。
10. 用真实 PDF 样本测试 OCR 和提取效果。

