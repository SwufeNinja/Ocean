# Ocean

Ocean 是一个面向学术材料整理的 PDF OCR 与文本提取工具。项目目标是批量处理 PDF，保留 OCR 文本与原 PDF 页码的对应关系，并基于关键词或 LLM 语义理解提取指定主题相关内容。

## 当前状态

当前已经完成可运行的 OCR MVP 主链路：

- 已完成需求文档：`docs/requirements.md`
- 已完成技术方案：`docs/technical-design.md`
- 已搭建 Python 命令行项目结构
- 已实现 MinerU v4 OCR client
- 已接入 PaddleOCR 官方 hosted async Job API
- 已实现 OpenAI-compatible LLM client
- 已实现 OCR JSON 到关键词提取的基础流程
- 已实现 Markdown / JSON / CSV 导出
- 已实现逐文件失败隔离、备用 OCR 引擎重试和批次运行报告
- 已实现 Web 上传、OCR 进度显示和 Markdown 阅读器初版

MinerU 已按官方 v4 API 接入：申请上传 URL、上传 PDF、轮询解析结果、下载 zip，并将 content_list.json 映射为按页组织的 OCR JSON。PaddleOCR 已按官方异步 Job API 接入。超过单次 API 页数限制的 PDF 会在本地自动切分，分段 OCR 后再合并回原 PDF 页码。

批量 OCR 中单个 PDF 失败不会中断后续文件。如果配置了 `ocr.fallback_engine`，主引擎失败后会自动尝试备用引擎。每次运行会生成 `outputs/ocr/run_report.json`，记录批次状态、每个文件的处理状态、尝试过的 OCR 引擎、错误信息、页数、时间和输出路径。

备用引擎的连接和参数可以放在 `ocr_engines.<engine>` 下；如果没有单独配置连接信息，PaddleOCR 和 MinerU 会尝试读取各自的环境变量。

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

## 本地启动前后端服务

开发调试时需要分别启动后端和前端：后端 FastAPI 监听 `127.0.0.1:8000`，前端 Vite 监听 `127.0.0.1:5173`，Vite 会把 `/api` 请求代理到后端。

以下命令以 Windows PowerShell 为例。首次启动前请先完成上面的安装和配置。

终端 1：启动后端服务：

```powershell
cd C:\ocean
.\.venv\Scripts\python.exe -m ocean.web --config .\config.yaml --output .\outputs --host 127.0.0.1 --port 8000
```

如果已经激活虚拟环境，也可以使用命令行入口：

```powershell
ocean web --config .\config.yaml --output .\outputs --host 127.0.0.1 --port 8000
```

终端 2：启动前端开发服务：

```powershell
cd C:\ocean\frontend
npm install
npm run dev
```

开发环境访问：

```text
http://127.0.0.1:5173/
```

如果只想让后端托管前端静态资源，先构建前端，再启动后端：

```powershell
cd C:\ocean\frontend
npm install
npm run build

cd C:\ocean
.\.venv\Scripts\python.exe -m ocean.web --config .\config.yaml --output .\outputs --host 127.0.0.1 --port 8000
```

构建后访问：

```text
http://127.0.0.1:8000/
```

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
    run_report.json
```

### Web 上传与 Markdown 阅读

开发环境按「本地启动前后端服务」同时启动后端和前端，然后访问 `http://127.0.0.1:5173/`。

如果已经执行过 `npm run build`，也可以只启动后端，由 FastAPI 托管前端静态资源：

```bash
ocean web --config ./config.yaml --output ./outputs --host 127.0.0.1 --port 8000
```

然后打开：

```text
http://127.0.0.1:8000/
```

页面支持上传 PDF、选择 PaddleOCR 或 MinerU、显示上传和 OCR 处理进度，并在处理完成后直接以 Markdown 阅读器方式展示结果。Web 流程目前只生成 Markdown，不额外导出 OCR JSON，也暂不支持关键词或 LLM 提取。

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

1. 完善结构化 OCR 任务状态和失败页记录。
2. 增强关键词提取：正则、简繁转换和更多提取粒度。
3. 增强 LLM 提取：单 chunk 失败隔离、prompt 配置化和返回结果校验。
4. 实现关键词与 LLM 结果的合并和去重。
5. 根据实际使用需求扩展 Web 任务历史、OCR JSON 下载和提取功能。

## 文档

- 需求文档：`docs/requirements.md`
- 技术方案：`docs/technical-design.md`
