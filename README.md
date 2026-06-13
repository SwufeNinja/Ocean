# Ocean

Ocean 是一个面向学术材料整理的 PDF OCR 与文本提取工具。项目目标是批量处理 PDF，保留 OCR 文本与原 PDF 页码的对应关系，并基于关键词或 LLM 语义理解提取指定主题相关内容。

## 当前状态

当前是 MVP 骨架阶段：

- 已完成需求文档：`docs/requirements.md`
- 已完成技术方案：`docs/technical-design.md`
- 已搭建 Python 命令行项目结构
- 已实现 MinerU v4 OCR client，并预留 PaddleOCR client
- 已实现 OpenAI-compatible LLM client
- 已实现 OCR JSON 到关键词提取的基础流程
- 已实现 Markdown / JSON / CSV 导出

MinerU 已按官方 v4 API 接入：申请上传 URL、上传 PDF、轮询解析结果、下载 zip，并将 content_list.json 映射为按页组织的 OCR JSON。超过 200 页的 PDF 会在本地自动切分，分段 OCR 后再合并回原 PDF 页码。PaddleOCR 的真实 API 映射后续再补。

## 安装

建议使用虚拟环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## 配置

复制示例配置：

```bash
cp config.example.yaml config.yaml
cp .env.example .env
```

然后填写：

- MinerU API token
- LLM API base URL
- LLM API key
- LLM model

也可以直接通过环境变量传入。

## 命令行用法

### 批量 OCR

```bash
ocean ocr --input ./pdfs --output ./outputs --config ./config.yaml
```

输出：

```text
outputs/
  ocr/
    example.md
    example.json
```

### Web 上传与 Markdown 阅读

启动最简单的 MinerU Web 前端：

```bash
ocean web --config ./config.yaml --output ./outputs --host 127.0.0.1 --port 8000
```

然后打开：

```text
http://127.0.0.1:8000
```

页面支持上传 PDF、显示上传和 OCR 处理进度，并在处理完成后直接以 Markdown 阅读器方式展示结果。Web 流程只生成 Markdown，不额外导出 OCR JSON。

### 关键词提取

```bash
ocean extract-keywords --ocr-dir ./outputs/ocr --output ./outputs --config ./config.yaml
```

输出：

```text
outputs/
  extract/
    keywords.md
    keywords.json
    keywords.csv
```

### LLM 语义提取

```bash
ocean extract-semantic --ocr-dir ./outputs/ocr --output ./outputs --config ./config.yaml
```

输出：

```text
outputs/
  extract/
    semantic.md
    semantic.json
    semantic.csv
```

## 进度日志

OCR 运行时会同时输出到终端，并写入：

```text
outputs/ocr_run.log
```

可以另开一个终端查看进度：

```bash
tail -f outputs/ocr_run.log
```

日志会显示：PDF 扫描、页数检测、是否切分、每个分卷上传、MinerU 轮询状态、结果下载、JSON/Markdown 导出。

## OCR JSON 格式

系统内部统一使用按页组织的 OCR JSON：

```json
{
  "source_file": "example.pdf",
  "source_path": "/path/to/example.pdf",
  "ocr_engine": "paddleocr",
  "pages": [
    {
      "page_number": 1,
      "text": "本页 OCR 文本",
      "blocks": []
    }
  ]
}
```

只要 PaddleOCR 或 MinerU 的结果能映射成这个结构，后续关键词提取和 LLM 语义提取都可以复用。

## 开发顺序

1. 准备一批典型 PDF 样本，包括扫描版、繁体、竖版、多栏报刊。
2. 使用真实 MinerU token 跑通 PDF OCR。
3. 检查 MinerU 输出的页码、段落、表格和 Markdown 质量。
4. 用 OCR JSON 测试关键词提取。
5. 配置 LLM API，测试语义提取效果。
6. 根据提取质量调整 chunk 大小、prompt 和输出格式。

## 文档

- 需求文档：`docs/requirements.md`
- 技术方案：`docs/technical-design.md`
