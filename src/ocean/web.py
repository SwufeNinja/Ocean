from __future__ import annotations

import copy
import math
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ocean.config import load_config
from ocean.exporters import write_ocr_markdown
from ocean.logging_utils import log, set_log_file
from ocean.ocr import create_ocr_client
from ocean.pdf_utils import count_pdf_pages, split_pdf
from ocean.pipeline import _merge_part_documents, _offset_document_pages

try:
    from fastapi import FastAPI, File, HTTPException, UploadFile
    from fastapi.responses import HTMLResponse, PlainTextResponse
except ImportError:  # pragma: no cover
    FastAPI = File = HTTPException = UploadFile = None  # type: ignore[assignment]
    HTMLResponse = PlainTextResponse = None  # type: ignore[assignment]


@dataclass(slots=True)
class WebJob:
    job_id: str
    file_name: str
    job_dir: Path
    input_path: Path
    log_path: Path
    state: str = "queued"
    progress: int = 0
    message: str = "等待处理"
    total_pages: int | None = None
    markdown_path: Path | None = None
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "file_name": self.file_name,
            "state": self.state,
            "progress": self.progress,
            "message": self.message,
            "total_pages": self.total_pages,
            "error": self.error,
            "markdown_url": f"/api/jobs/{self.job_id}/markdown" if self.markdown_path else None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def make_app(config: dict[str, Any], output_dir: str | Path = "./outputs"):
    if FastAPI is None or File is None or HTTPException is None or UploadFile is None:
        raise RuntimeError("Web UI dependencies are missing. Run: pip install -e .")

    app = FastAPI(title="Ocean MinerU Web")
    output_root = Path(output_dir).expanduser().resolve()
    web_root = output_root / "web_jobs"
    upload_root = output_root / "web_uploads"
    jobs: dict[str, WebJob] = {}
    jobs_lock = threading.Lock()
    ocr_lock = threading.Lock()

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return INDEX_HTML

    @app.post("/api/jobs")
    async def create_job(file: UploadFile = File(...)) -> dict[str, Any]:
        original_filename = Path(file.filename or "").name
        if not original_filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="只支持 PDF 文件")
        filename = _safe_pdf_name(original_filename)

        job_id = uuid.uuid4().hex[:12]
        job_dir = web_root / job_id
        upload_dir = upload_root / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        upload_dir.mkdir(parents=True, exist_ok=True)
        input_path = upload_dir / filename

        with input_path.open("wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)

        now = _now()
        job = WebJob(
            job_id=job_id,
            file_name=filename,
            job_dir=job_dir,
            input_path=input_path,
            log_path=job_dir / "ocr_run.log",
            created_at=now,
            updated_at=now,
        )
        with jobs_lock:
            jobs[job_id] = job

        thread = threading.Thread(
            target=_run_job,
            args=(job_id, jobs, jobs_lock, ocr_lock, config),
            daemon=True,
        )
        thread.start()
        return job.to_dict()

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        job = _get_job_or_404(job_id, jobs, jobs_lock)
        data = job.to_dict()
        data["log_tail"] = _read_log_tail(job.log_path)
        return data

    @app.get("/api/jobs/{job_id}/markdown", response_class=PlainTextResponse)
    def get_markdown(job_id: str) -> str:
        job = _get_job_or_404(job_id, jobs, jobs_lock)
        if job.state != "done" or not job.markdown_path or not job.markdown_path.exists():
            raise HTTPException(status_code=404, detail="Markdown 还没有生成")
        return job.markdown_path.read_text(encoding="utf-8")

    return app


def serve(config: dict[str, Any], output_dir: str | Path, host: str = "127.0.0.1", port: int = 8000) -> None:
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("uvicorn is missing. Run: pip install -e .") from exc

    app = make_app(config=config, output_dir=output_dir)
    uvicorn.run(app, host=host, port=port)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="ocean-web", description="Start Ocean MinerU web UI.")
    parser.add_argument("--config", required=True, help="YAML config path.")
    parser.add_argument("--output", default="./outputs", help="Output directory.")
    parser.add_argument("--host", default="127.0.0.1", help="Web server host.")
    parser.add_argument("--port", type=int, default=8000, help="Web server port.")
    args = parser.parse_args()

    serve(config=load_config(args.config), output_dir=args.output, host=args.host, port=args.port)


def _run_job(
    job_id: str,
    jobs: dict[str, WebJob],
    jobs_lock: threading.Lock,
    ocr_lock: threading.Lock,
    config: dict[str, Any],
) -> None:
    _update_job(jobs, jobs_lock, job_id, state="queued", progress=1, message="排队等待 OCR 任务")
    with ocr_lock:
        job = _get_job(job_id, jobs, jobs_lock)
        if not job:
            return
        set_log_file(job.log_path)
        try:
            _recognize_job(job_id, jobs, jobs_lock, config)
        except Exception as exc:  # pragma: no cover - depends on external OCR service
            log(f"Web OCR failed: {exc}")
            _update_job(jobs, jobs_lock, job_id, state="failed", progress=100, message="处理失败", error=str(exc))
        finally:
            set_log_file(None)


def _recognize_job(
    job_id: str,
    jobs: dict[str, WebJob],
    jobs_lock: threading.Lock,
    config: dict[str, Any],
) -> None:
    job = _get_job(job_id, jobs, jobs_lock)
    if not job:
        return

    ocr_config = copy.deepcopy(config.get("ocr", {}))
    ocr_config["engine"] = "mineru"
    ocr_config["output_json"] = False
    ocr_config["output_markdown"] = True
    options = ocr_config.get("options", {})
    max_pages = int(options.get("max_pages_per_file", 200))
    client = create_ocr_client(ocr_config)

    _update_job(jobs, jobs_lock, job_id, state="running", progress=3, message="读取 PDF 页数")
    total_pages = count_pdf_pages(job.input_path)
    _update_job(jobs, jobs_lock, job_id, total_pages=total_pages)
    log(f"Web OCR started: {job.file_name}; pages={total_pages}; max_pages_per_file={max_pages}.")

    if total_pages <= max_pages:
        _update_job(jobs, jobs_lock, job_id, progress=12, message="上传 MinerU 并等待解析")
        document = client.recognize_pdf(job.input_path, options)
        _update_job(jobs, jobs_lock, job_id, progress=88, message="MinerU 解析完成，正在生成 Markdown")
    else:
        with TemporaryDirectory(prefix="ocean_web_pdf_split_") as temp_dir:
            parts = split_pdf(job.input_path, temp_dir, max_pages=max_pages)
            log(f"Web OCR split into {len(parts)} part(s).")
            part_documents = []
            for index, part in enumerate(parts, start=1):
                start_progress = _part_progress(index - 1, len(parts))
                _update_job(
                    jobs,
                    jobs_lock,
                    job_id,
                    progress=start_progress,
                    message=f"正在处理第 {index}/{len(parts)} 段（原 PDF 第 {part.page_start}-{part.page_end} 页）",
                )
                log(f"Web OCR part {index}/{len(parts)}: original pages {part.page_start}-{part.page_end}.")
                part_document = client.recognize_pdf(part.path, options)
                _offset_document_pages(part_document, part)
                part_documents.append(part_document)
                _update_job(
                    jobs,
                    jobs_lock,
                    job_id,
                    progress=_part_progress(index, len(parts)),
                    message=f"第 {index}/{len(parts)} 段完成",
                )
            document = _merge_part_documents(job.input_path, part_documents, total_pages, max_pages)

    markdown_path = job.job_dir / f"{Path(job.file_name).stem}.md"
    write_ocr_markdown(document, markdown_path)
    log(f"Web OCR markdown exported: {markdown_path}")
    _update_job(
        jobs,
        jobs_lock,
        job_id,
        state="done",
        progress=100,
        message="处理完成",
        markdown_path=markdown_path,
    )


def _part_progress(completed_parts: int, total_parts: int) -> int:
    if total_parts <= 0:
        return 10
    return min(88, 8 + math.floor((completed_parts / total_parts) * 80))


def _update_job(
    jobs: dict[str, WebJob],
    jobs_lock: threading.Lock,
    job_id: str,
    **changes: Any,
) -> None:
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return
        for key, value in changes.items():
            setattr(job, key, value)
        job.updated_at = _now()


def _get_job(job_id: str, jobs: dict[str, WebJob], jobs_lock: threading.Lock) -> WebJob | None:
    with jobs_lock:
        return jobs.get(job_id)


def _get_job_or_404(job_id: str, jobs: dict[str, WebJob], jobs_lock: threading.Lock) -> WebJob:
    job = _get_job(job_id, jobs, jobs_lock)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


def _read_log_tail(path: Path, max_lines: int = 80) -> list[str]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-max_lines:]


def _safe_pdf_name(filename: str) -> str:
    name = Path(filename).name
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._")
    if not name:
        name = "upload.pdf"
    return name


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


INDEX_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Ocean MinerU Markdown Reader</title>
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/dompurify/dist/purify.min.js"></script>
  <style>
    :root {
      --ink: #1e2a24;
      --muted: #66756d;
      --paper: #fbf7ed;
      --card: rgba(255, 252, 243, 0.92);
      --line: rgba(33, 49, 42, 0.14);
      --accent: #d96b2b;
      --accent-2: #245f73;
      --good: #287a4f;
      --bad: #a53b2c;
      --shadow: 0 24px 80px rgba(36, 47, 41, 0.18);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      color: var(--ink);
      font-family: "Iowan Old Style", "Songti SC", "Noto Serif CJK SC", Georgia, serif;
      background:
        radial-gradient(circle at 8% 12%, rgba(217, 107, 43, 0.20), transparent 24rem),
        radial-gradient(circle at 88% 4%, rgba(36, 95, 115, 0.24), transparent 28rem),
        linear-gradient(135deg, #efe5cf 0%, #f8f1df 42%, #e6efe8 100%);
    }
    body::before {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background-image: linear-gradient(rgba(30, 42, 36, 0.05) 1px, transparent 1px);
      background-size: 100% 34px;
      mix-blend-mode: multiply;
    }
    .shell {
      width: min(1180px, calc(100% - 32px));
      margin: 0 auto;
      padding: 44px 0 72px;
    }
    .hero {
      display: grid;
      grid-template-columns: 0.95fr 1.05fr;
      gap: 24px;
      align-items: stretch;
    }
    .panel {
      border: 1px solid var(--line);
      border-radius: 28px;
      background: var(--card);
      box-shadow: var(--shadow);
      backdrop-filter: blur(14px);
    }
    .intro {
      padding: 34px;
      overflow: hidden;
      position: relative;
    }
    .intro::after {
      content: "MinerU";
      position: absolute;
      right: -18px;
      bottom: -30px;
      color: rgba(36, 95, 115, 0.08);
      font-size: 108px;
      font-weight: 800;
      letter-spacing: -0.08em;
    }
    .eyebrow {
      display: inline-flex;
      gap: 8px;
      align-items: center;
      padding: 7px 12px;
      border-radius: 999px;
      color: var(--accent-2);
      background: rgba(36, 95, 115, 0.10);
      font: 700 12px/1.1 "Avenir Next", "PingFang SC", sans-serif;
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }
    h1 {
      margin: 22px 0 14px;
      max-width: 620px;
      font-size: clamp(36px, 6vw, 70px);
      line-height: 0.94;
      letter-spacing: -0.065em;
    }
    .lede {
      position: relative;
      z-index: 1;
      max-width: 620px;
      margin: 0;
      color: var(--muted);
      font-size: 18px;
      line-height: 1.8;
    }
    .upload {
      padding: 24px;
    }
    .drop {
      display: grid;
      place-items: center;
      min-height: 230px;
      padding: 28px;
      border: 2px dashed rgba(36, 95, 115, 0.36);
      border-radius: 24px;
      background:
        linear-gradient(135deg, rgba(255,255,255,0.60), rgba(255,255,255,0.18)),
        repeating-linear-gradient(-45deg, transparent 0 12px, rgba(217,107,43,0.06) 12px 24px);
      text-align: center;
      transition: 180ms ease;
    }
    .drop.dragging {
      border-color: var(--accent);
      transform: translateY(-2px);
      background-color: rgba(217, 107, 43, 0.08);
    }
    .drop strong {
      display: block;
      margin-bottom: 8px;
      font-size: 24px;
      letter-spacing: -0.03em;
    }
    .drop span {
      color: var(--muted);
      font-family: "Avenir Next", "PingFang SC", sans-serif;
      font-size: 14px;
    }
    input[type=file] { display: none; }
    .actions {
      display: flex;
      gap: 12px;
      align-items: center;
      margin-top: 18px;
      flex-wrap: wrap;
    }
    button, .file-button {
      border: 0;
      border-radius: 999px;
      padding: 13px 18px;
      color: #fff;
      background: var(--ink);
      font: 800 14px/1 "Avenir Next", "PingFang SC", sans-serif;
      cursor: pointer;
      box-shadow: 0 10px 24px rgba(30, 42, 36, 0.18);
    }
    button.secondary, .file-button {
      color: var(--ink);
      background: rgba(30, 42, 36, 0.10);
      box-shadow: none;
    }
    button:disabled {
      cursor: not-allowed;
      opacity: 0.52;
    }
    .file-name {
      color: var(--muted);
      font: 700 13px/1.4 "Avenir Next", "PingFang SC", sans-serif;
    }
    .status {
      margin-top: 24px;
      padding: 20px;
      border-radius: 22px;
      background: rgba(255, 255, 255, 0.45);
      border: 1px solid var(--line);
    }
    .status-line {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      color: var(--muted);
      font: 800 13px/1.4 "Avenir Next", "PingFang SC", sans-serif;
    }
    .bar {
      height: 15px;
      margin-top: 12px;
      overflow: hidden;
      border-radius: 999px;
      background: rgba(30, 42, 36, 0.10);
    }
    .bar > i {
      display: block;
      width: 0%;
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, var(--accent), #efb247, var(--accent-2));
      transition: width 420ms ease;
    }
    .reader {
      margin-top: 24px;
      padding: clamp(22px, 4vw, 46px);
    }
    .reader-head {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: baseline;
      padding-bottom: 18px;
      border-bottom: 1px solid var(--line);
    }
    .reader h2 {
      margin: 0;
      font-size: clamp(24px, 3vw, 38px);
      letter-spacing: -0.045em;
    }
    .hint {
      color: var(--muted);
      font: 700 13px/1.5 "Avenir Next", "PingFang SC", sans-serif;
    }
    .markdown {
      max-width: 880px;
      margin: 28px auto 0;
      font-size: 18px;
      line-height: 1.86;
    }
    .markdown h1, .markdown h2, .markdown h3 {
      letter-spacing: -0.04em;
      line-height: 1.1;
    }
    .markdown h1 { font-size: 38px; }
    .markdown h2 {
      margin-top: 42px;
      padding-top: 24px;
      border-top: 1px solid var(--line);
      color: var(--accent-2);
      font-size: 28px;
    }
    .markdown p { margin: 0 0 1em; }
    .markdown table {
      width: 100%;
      border-collapse: collapse;
      margin: 20px 0;
      font-size: 15px;
    }
    .markdown th, .markdown td {
      border: 1px solid var(--line);
      padding: 10px;
      vertical-align: top;
    }
    .markdown pre {
      overflow: auto;
      padding: 18px;
      border-radius: 16px;
      background: rgba(30, 42, 36, 0.08);
    }
    .logs {
      max-height: 180px;
      margin-top: 16px;
      padding: 12px;
      overflow: auto;
      border-radius: 14px;
      color: #516159;
      background: rgba(30, 42, 36, 0.06);
      font: 12px/1.6 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      white-space: pre-wrap;
    }
    .is-hidden { display: none; }
    @media (max-width: 860px) {
      .hero { grid-template-columns: 1fr; }
      .shell { padding-top: 22px; }
      .intro, .upload { padding: 22px; }
      .reader-head { display: block; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div class="panel intro">
        <span class="eyebrow">Ocean OCR</span>
        <h1>上传 PDF，等待 MinerU，直接读 Markdown。</h1>
        <p class="lede">这个页面只走 MinerU 版 OCR 链路。超过 200 页的 PDF 会按配置自动切分，处理完成后只展示 Markdown 阅读器，不导出 JSON。</p>
      </div>

      <form class="panel upload" id="uploadForm">
        <label class="drop" id="dropZone" for="pdfFile">
          <span>
            <strong>把 PDF 拖到这里</strong>
            或点击选择文件，支持大 PDF 自动分段处理
          </span>
        </label>
        <input id="pdfFile" name="file" type="file" accept="application/pdf,.pdf" />
        <div class="actions">
          <label class="file-button" for="pdfFile">选择 PDF</label>
          <button id="startBtn" type="submit" disabled>开始处理</button>
          <span class="file-name" id="fileName">还没有选择文件</span>
        </div>
        <div class="status" id="statusBox">
          <div class="status-line">
            <span id="statusText">等待上传</span>
            <span id="percentText">0%</span>
          </div>
          <div class="bar"><i id="progressBar"></i></div>
          <div class="logs is-hidden" id="logs"></div>
        </div>
      </form>
    </section>

    <section class="panel reader is-hidden" id="reader">
      <div class="reader-head">
        <h2>Markdown 阅读器</h2>
        <span class="hint" id="readerHint">处理完成后会自动显示</span>
      </div>
      <article class="markdown" id="markdownView"></article>
    </section>
  </main>

  <script>
    const form = document.getElementById("uploadForm");
    const fileInput = document.getElementById("pdfFile");
    const fileName = document.getElementById("fileName");
    const startBtn = document.getElementById("startBtn");
    const statusText = document.getElementById("statusText");
    const percentText = document.getElementById("percentText");
    const progressBar = document.getElementById("progressBar");
    const logs = document.getElementById("logs");
    const reader = document.getElementById("reader");
    const readerHint = document.getElementById("readerHint");
    const markdownView = document.getElementById("markdownView");
    const dropZone = document.getElementById("dropZone");

    let activeJob = null;
    let pollTimer = null;

    function setProgress(percent, text) {
      const safe = Math.max(0, Math.min(100, Number(percent) || 0));
      progressBar.style.width = `${safe}%`;
      percentText.textContent = `${Math.round(safe)}%`;
      if (text) statusText.textContent = text;
    }

    function setFile(file) {
      if (!file) return;
      const dataTransfer = new DataTransfer();
      dataTransfer.items.add(file);
      fileInput.files = dataTransfer.files;
      fileName.textContent = `${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} MB`;
      startBtn.disabled = false;
    }

    fileInput.addEventListener("change", () => {
      const file = fileInput.files[0];
      if (!file) return;
      fileName.textContent = `${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} MB`;
      startBtn.disabled = false;
    });

    ["dragenter", "dragover"].forEach((eventName) => {
      dropZone.addEventListener(eventName, (event) => {
        event.preventDefault();
        dropZone.classList.add("dragging");
      });
    });

    ["dragleave", "drop"].forEach((eventName) => {
      dropZone.addEventListener(eventName, (event) => {
        event.preventDefault();
        dropZone.classList.remove("dragging");
      });
    });

    dropZone.addEventListener("drop", (event) => {
      const file = event.dataTransfer.files[0];
      if (file) setFile(file);
    });

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const file = fileInput.files[0];
      if (!file) return;
      startUpload(file);
    });

    function startUpload(file) {
      startBtn.disabled = true;
      reader.classList.add("is-hidden");
      markdownView.innerHTML = "";
      logs.classList.remove("is-hidden");
      logs.textContent = "";
      setProgress(0, "正在上传 PDF");

      const formData = new FormData();
      formData.append("file", file);
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/jobs");
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) {
          setProgress(Math.round((event.loaded / event.total) * 8), "正在上传 PDF");
        }
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          activeJob = JSON.parse(xhr.responseText);
          setProgress(activeJob.progress || 8, activeJob.message || "已提交 OCR 任务");
          pollJob(activeJob.job_id);
        } else {
          setProgress(100, "上传失败");
          startBtn.disabled = false;
          logs.textContent = xhr.responseText || "上传失败";
        }
      };
      xhr.onerror = () => {
        setProgress(100, "网络错误");
        startBtn.disabled = false;
      };
      xhr.send(formData);
    }

    async function pollJob(jobId) {
      if (pollTimer) clearTimeout(pollTimer);
      try {
        const response = await fetch(`/api/jobs/${jobId}`);
        const job = await response.json();
        setProgress(job.progress, job.message);
        if (job.total_pages) {
          readerHint.textContent = `${job.file_name} · ${job.total_pages} 页`;
        }
        if (job.log_tail && job.log_tail.length) {
          logs.textContent = job.log_tail.join("\\n");
          logs.scrollTop = logs.scrollHeight;
        }
        if (job.state === "done") {
          await loadMarkdown(job.markdown_url);
          startBtn.disabled = false;
          return;
        }
        if (job.state === "failed") {
          setProgress(100, job.error || "处理失败");
          startBtn.disabled = false;
          return;
        }
        pollTimer = setTimeout(() => pollJob(jobId), 2000);
      } catch (error) {
        statusText.textContent = `轮询失败：${error.message}`;
        pollTimer = setTimeout(() => pollJob(jobId), 4000);
      }
    }

    async function loadMarkdown(url) {
      const response = await fetch(url);
      const markdown = await response.text();
      if (window.marked && window.DOMPurify) {
        markdownView.innerHTML = DOMPurify.sanitize(marked.parse(markdown));
      } else {
        markdownView.textContent = markdown;
      }
      reader.classList.remove("is-hidden");
      reader.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  </script>
</body>
</html>
"""
