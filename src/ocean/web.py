from __future__ import annotations

import copy
import math
import os
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
from ocean.extractors import extract_keywords
from ocean.logging_utils import log, set_log_file
from ocean.models import ExtractionResult, OcrDocument
from ocean.ocr import create_ocr_client
from ocean.pdf_utils import count_pdf_pages, split_pdf
from ocean.pipeline import _merge_part_documents, _offset_document_pages

try:
    from fastapi import FastAPI, File, Form, HTTPException, UploadFile
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
except ImportError:  # pragma: no cover
    FastAPI = File = Form = HTTPException = UploadFile = None  # type: ignore[assignment]
    StaticFiles = None  # type: ignore[assignment]
    FileResponse = HTMLResponse = PlainTextResponse = None  # type: ignore[assignment]


ENGINE_LABELS = {
    "paddleocr": "PaddleOCR",
    "mineru": "MinerU",
}
ENGINE_ALIASES = {
    "paddle": "paddleocr",
    "paddleocr": "paddleocr",
    "mineru": "mineru",
    "mineruocr": "mineru",
}


@dataclass(slots=True)
class WebJob:
    job_id: str
    file_name: str
    engine: str
    job_dir: Path
    input_path: Path
    log_path: Path
    state: str = "queued"
    progress: int = 0
    message: str = "等待处理"
    total_pages: int | None = None
    markdown_path: Path | None = None
    ocr_document: OcrDocument | None = None
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        markdown_url = f"/api/jobs/{self.job_id}/markdown" if self.markdown_path else None
        download_url = f"/api/jobs/{self.job_id}/download" if self.markdown_path else None
        pages_url = f"/api/jobs/{self.job_id}/pages" if self.ocr_document else None
        return {
            "job_id": self.job_id,
            "file_name": self.file_name,
            "engine": self.engine,
            "engine_label": ENGINE_LABELS.get(self.engine, self.engine),
            "state": self.state,
            "progress": self.progress,
            "message": self.message,
            "total_pages": self.total_pages,
            "error": self.error,
            "markdown_url": markdown_url,
            "download_url": download_url,
            "pages_url": pages_url,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def make_app(config: dict[str, Any], output_dir: str | Path = "./outputs"):
    if FastAPI is None or File is None or Form is None or HTTPException is None or UploadFile is None:
        raise RuntimeError("Web UI dependencies are missing. Run: pip install -e .")

    app = FastAPI(title="Ocean OCR Web")
    frontend_dist = _frontend_dist_root()
    frontend_index = frontend_dist / "index.html"
    frontend_assets = frontend_dist / "assets"
    if StaticFiles is not None and frontend_assets.exists():
        app.mount("/assets", StaticFiles(directory=frontend_assets), name="assets")

    output_root = Path(output_dir).expanduser().resolve()
    web_root = output_root / "web_jobs"
    upload_root = output_root / "web_uploads"
    jobs: dict[str, WebJob] = {}
    jobs_lock = threading.Lock()
    ocr_lock = threading.Lock()

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        if not frontend_index.exists():
            raise HTTPException(
                status_code=503,
                detail="Frontend build is missing. Run npm run build in the frontend directory.",
            )
        return frontend_index.read_text(encoding="utf-8")

    @app.get("/api/engines")
    def list_engines() -> dict[str, Any]:
        return {
            "default_engine": "paddleocr",
            "engines": [
                {"value": "paddleocr", "label": "PaddleOCR", "description": "默认：适合快速文档 OCR"},
                {"value": "mineru", "label": "MinerU", "description": "适合版面复杂的长文档解析"},
            ],
        }

    @app.post("/api/jobs")
    async def create_job(file: UploadFile = File(...), engine: str = Form("paddleocr")) -> dict[str, Any]:
        selected_engine = _normalize_engine(engine)
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
            engine=selected_engine,
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

    @app.get("/api/jobs/{job_id}/pages")
    def get_pages(job_id: str) -> dict[str, Any]:
        job = _get_job_or_404(job_id, jobs, jobs_lock)
        if job.state != "done" or job.ocr_document is None:
            raise HTTPException(status_code=404, detail="OCR pages are not ready")
        return {
            "source_file": job.ocr_document.source_file,
            "total_pages": len(job.ocr_document.pages),
            "pages": [
                {"page_number": page.page_number, "markdown": page.text.strip()}
                for page in job.ocr_document.pages
            ],
        }

    @app.get("/api/jobs/{job_id}/download")
    def download_markdown(job_id: str):
        job = _get_job_or_404(job_id, jobs, jobs_lock)
        if job.state != "done" or not job.markdown_path or not job.markdown_path.exists():
            raise HTTPException(status_code=404, detail="Markdown 还没有生成")
        return FileResponse(
            path=job.markdown_path,
            media_type="text/markdown; charset=utf-8",
            filename=job.markdown_path.name,
        )

    @app.post("/api/jobs/{job_id}/extract-keywords")
    def extract_job_keywords(
        job_id: str,
        keywords: str = Form(...),
        match_mode: str = Form("any"),
        context_before: int = Form(1),
        context_after: int = Form(1),
        granularity: str = Form("paragraph"),
        use_regex: bool = Form(False),
        case_sensitive: bool = Form(True),
        normalize_chinese: bool = Form(False),
        deduplicate: bool = Form(True),
    ) -> dict[str, Any]:
        job = _get_job_or_404(job_id, jobs, jobs_lock)
        if job.state != "done" or job.ocr_document is None:
            raise HTTPException(status_code=400, detail="OCR 完成后才能提取关键词")

        parsed_keywords = _parse_keywords(keywords)
        if not parsed_keywords:
            raise HTTPException(status_code=400, detail="请至少输入一个关键词")
        normalized_mode = str(match_mode or "any").lower()
        if normalized_mode not in {"any", "all"}:
            raise HTTPException(status_code=400, detail="match_mode 只支持 any 或 all")
        normalized_granularity = str(granularity or "paragraph").lower()
        if normalized_granularity not in {"paragraph", "page"}:
            raise HTTPException(status_code=400, detail="granularity 只支持 paragraph 或 page")

        results = extract_keywords(
            document=job.ocr_document,
            keywords=parsed_keywords,
            match_mode=normalized_mode,
            context_before=max(0, int(context_before)),
            context_after=max(0, int(context_after)),
            granularity=normalized_granularity,
            use_regex=bool(use_regex),
            case_sensitive=bool(case_sensitive),
            normalize_chinese=bool(normalize_chinese),
            deduplicate=bool(deduplicate),
        )
        return {
            "keywords": parsed_keywords,
            "match_mode": normalized_mode,
            "granularity": normalized_granularity,
            "use_regex": bool(use_regex),
            "case_sensitive": bool(case_sensitive),
            "normalize_chinese": bool(normalize_chinese),
            "deduplicate": bool(deduplicate),
            "count": len(results),
            "markdown": _keyword_results_markdown(results, parsed_keywords, normalized_mode),
            "results": [result.to_dict() for result in results],
        }

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

    parser = argparse.ArgumentParser(prog="ocean-web", description="Start Ocean OCR web UI.")
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

    ocr_config = _web_ocr_config(config, job.engine)
    options = ocr_config.get("options", {})
    max_pages = int(options.get("max_pages_per_file", 200))
    client = create_ocr_client(ocr_config)
    engine_label = ENGINE_LABELS.get(job.engine, job.engine)

    _update_job(jobs, jobs_lock, job_id, state="running", progress=3, message="读取 PDF 页数")
    total_pages = count_pdf_pages(job.input_path)
    _update_job(jobs, jobs_lock, job_id, total_pages=total_pages)
    log(f"Web OCR started: {job.file_name}; engine={job.engine}; pages={total_pages}; max_pages_per_file={max_pages}.")

    if total_pages <= max_pages:
        _update_job(jobs, jobs_lock, job_id, progress=12, message=f"提交到 {engine_label} 并等待解析")
        document = client.recognize_pdf(job.input_path, options)
        _update_job(jobs, jobs_lock, job_id, progress=88, message=f"{engine_label} 解析完成，正在生成 Markdown")
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
        ocr_document=document,
    )


def _web_ocr_config(config: dict[str, Any], engine: str) -> dict[str, Any]:
    ocr_config = copy.deepcopy(config.get("ocr", {}))
    engine_configs = copy.deepcopy(config.get("ocr_engines", {}))
    current_engine = _normalize_engine(ocr_config.get("engine", "paddleocr"))

    if engine in engine_configs and isinstance(engine_configs[engine], dict):
        merged = copy.deepcopy(ocr_config)
        merged.update(engine_configs[engine])
        ocr_config = merged
    elif current_engine != engine:
        # When switching engines in the UI, prefer environment variables over the
        # single CLI-oriented config block so both providers can coexist.
        ocr_config["api_base_url"] = ""
        ocr_config["api_token"] = ""

    ocr_config["engine"] = engine
    ocr_config["output_json"] = False
    ocr_config["output_markdown"] = True
    ocr_config.setdefault("options", {})

    if engine == "paddleocr":
        ocr_config["api_base_url"] = (
            ocr_config.get("api_base_url")
            or os.getenv("PADDLEOCR_API_BASE_URL")
            or "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
        )
        ocr_config["api_token"] = ocr_config.get("api_token") or os.getenv("PADDLEOCR_API_TOKEN", "")
        options = ocr_config["options"]
        options.setdefault("model", "PaddleOCR-VL-1.6")
        options.setdefault("auth_scheme", "bearer")
        options.setdefault("timeout_seconds", 600)
        options.setdefault("poll_interval_seconds", 5)
        options.setdefault("max_wait_seconds", 1800)
        options.setdefault("download_timeout_seconds", 600)
        options.setdefault("max_pages_per_file", 10)
    elif engine == "mineru":
        ocr_config["api_base_url"] = ocr_config.get("api_base_url") or os.getenv("MINERU_API_BASE_URL") or "https://mineru.net"
        ocr_config["api_token"] = ocr_config.get("api_token") or os.getenv("MINERU_API_TOKEN", "")
        options = ocr_config["options"]
        options.setdefault("model_version", "vlm")
        options.setdefault("language", "ch")
        options.setdefault("is_ocr", True)
        options.setdefault("enable_table", True)
        options.setdefault("enable_formula", True)
        options.setdefault("poll_interval_seconds", 5)
        options.setdefault("max_wait_seconds", 1800)
        options.setdefault("download_timeout_seconds", 120)
        options.setdefault("max_pages_per_file", 200)
    return ocr_config


def _normalize_engine(engine: Any) -> str:
    normalized = ENGINE_ALIASES.get(str(engine or "paddleocr").strip().lower())
    if not normalized:
        raise HTTPException(status_code=400, detail=f"不支持的 OCR 引擎：{engine}")
    return normalized


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


def _parse_keywords(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,，;；\n]+", value) if item.strip()]


def _frontend_dist_root() -> Path:
    return Path(__file__).resolve().parents[2] / "frontend" / "dist"




def _keyword_results_markdown(results: list[ExtractionResult], keywords: list[str], match_mode: str) -> str:
    lines = [
        "# 关键词提取结果",
        "",
        f"- 关键词：{', '.join(keywords)}",
        f"- 匹配模式：{match_mode}",
        f"- 命中结果：{len(results)} 条",
        "",
    ]
    for result in results:
        page_label = (
            f"第 {result.page_start}-{result.page_end} 页"
            if result.page_start != result.page_end
            else f"第 {result.page_start} 页"
        )
        lines.extend(
            [
                f"## {result.result_id}",
                "",
                f"- 来源文件：{result.source_file}",
                f"- 页码：{page_label}",
                f"- 提取方式：{result.extraction_method}",
                f"- 命中关键词：{', '.join(result.matched_keywords)}",
                "",
                result.text.strip(),
                "",
            ]
        )
    return "\n".join(lines)
def _safe_pdf_name(filename: str) -> str:
    name = Path(filename).name
    stem = re.sub(r"[^\w.()（）\[\]【】 -]+", "_", name, flags=re.UNICODE).strip(" ._")
    return stem or "upload.pdf"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")



if __name__ == "__main__":
    main()

