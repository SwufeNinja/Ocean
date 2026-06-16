from __future__ import annotations

import copy
import hashlib
import math
import os
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import quote

from ocean.config import load_config
from ocean.exporters import write_ocr_markdown
from ocean.extractors import extract_keywords
from ocean.llm.client import OpenAICompatibleClient
from ocean.logging_utils import log, set_log_file
from ocean.models import ExtractionResult, OcrDocument
from ocean.ocr import create_ocr_client
from ocean.pdf_utils import count_pdf_pages, split_pdf
from ocean.pipeline import _merge_part_documents, _offset_document_pages
from ocean.storage import ElasticsearchDocumentStore, create_document_store
from ocean.storage.elasticsearch import (
    DEFAULT_ACCOUNT_ID,
    DEFAULT_KNOWLEDGE_BASE_ID,
    PIPELINE_VERSION,
    build_processing_fingerprint,
    compute_file_sha256,
    options_hash,
)

try:
    from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
except ImportError:  # pragma: no cover
    Body = FastAPI = File = Form = HTTPException = UploadFile = None  # type: ignore[assignment]
    StaticFiles = None  # type: ignore[assignment]
    FileResponse = HTMLResponse = PlainTextResponse = None  # type: ignore[assignment]


ENGINE_LABELS = {
    "paddleocr": "PaddleOCR",
    "mineru": "MinerU",
}
DEFAULT_ENGINE = "mineru"
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
    account_id: str = DEFAULT_ACCOUNT_ID
    knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID
    document_id: str | None = None
    file_sha256: str | None = None
    processing_fingerprint: str | None = None
    reused: bool = False
    batch_id: str | None = None
    queue_index: int | None = None
    queue_total: int | None = None
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
        has_done_result = self.state == "done" and (self.markdown_path or self.document_id)
        markdown_url = f"/api/jobs/{self.job_id}/markdown" if has_done_result else None
        download_url = f"/api/jobs/{self.job_id}/download" if has_done_result else None
        pages_url = f"/api/jobs/{self.job_id}/pages" if self.state == "done" and (self.ocr_document or self.document_id) else None
        return {
            "job_id": self.job_id,
            "account_id": self.account_id,
            "knowledge_base_id": self.knowledge_base_id,
            "document_id": self.document_id,
            "file_sha256": self.file_sha256,
            "processing_fingerprint": self.processing_fingerprint,
            "reused": self.reused,
            "batch_id": self.batch_id,
            "file_name": self.file_name,
            "engine": self.engine,
            "engine_label": ENGINE_LABELS.get(self.engine, self.engine),
            "queue_index": self.queue_index,
            "queue_total": self.queue_total,
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


@dataclass(slots=True)
class ChatMessage:
    role: str
    content: str
    created_at: str = field(default_factory=lambda: _now())
    message_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at,
        }


@dataclass(slots=True)
class LlmConversation:
    conversation_id: str
    title: str
    account_id: str = DEFAULT_ACCOUNT_ID
    knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID
    system_prompt: str = ""
    messages: list[ChatMessage] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: _now())
    updated_at: str = field(default_factory=lambda: _now())

    def to_dict(self, *, include_messages: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "conversation_id": self.conversation_id,
            "account_id": self.account_id,
            "knowledge_base_id": self.knowledge_base_id,
            "title": self.title,
            "system_prompt": self.system_prompt,
            "message_count": len(self.messages),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if include_messages:
            data["messages"] = [message.to_dict() for message in self.messages]
        return data


def make_app(config: dict[str, Any], output_dir: str | Path = "./outputs"):
    if Body is None or FastAPI is None or File is None or Form is None or HTTPException is None or UploadFile is None:
        raise RuntimeError("Web UI dependencies are missing. Run: pip install -e .")

    app = FastAPI(title="Ocean OCR Web")
    frontend_dist = _frontend_dist_root()
    frontend_index = frontend_dist / "index.html"
    frontend_assets = frontend_dist / "assets"
    if StaticFiles is not None:
        app.mount("/assets", StaticFiles(directory=frontend_assets, check_dir=False), name="assets")

    output_root = Path(output_dir).expanduser().resolve()
    web_root = output_root / "web_jobs"
    upload_root = output_root / "web_uploads"
    document_store = create_document_store(config.get("elasticsearch", {}))
    jobs: dict[str, WebJob] = {}
    jobs_lock = threading.Lock()
    ocr_lock = threading.Lock()
    llm_conversations: dict[str, LlmConversation] = {}
    llm_lock = threading.Lock()

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
            "default_engine": DEFAULT_ENGINE,
            "engines": [
                {"value": "mineru", "label": "MinerU", "description": "默认：适合版面复杂的长文档解析"},
                {"value": "paddleocr", "label": "PaddleOCR", "description": "适合快速文档 OCR"},
            ],
        }

    @app.get("/api/documents")
    def list_documents(
        account_id: str = DEFAULT_ACCOUNT_ID,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
        q: str = "",
        limit: int = 100,
    ) -> dict[str, Any]:
        store = _require_document_store(document_store)
        scoped_account_id = _scope_id(account_id, DEFAULT_ACCOUNT_ID)
        scoped_knowledge_base_id = _scope_id(knowledge_base_id, DEFAULT_KNOWLEDGE_BASE_ID)
        records = store.list_documents(
            account_id=scoped_account_id,
            knowledge_base_id=scoped_knowledge_base_id,
            query_text=q,
            limit=limit,
        )
        return {
            "account_id": scoped_account_id,
            "knowledge_base_id": scoped_knowledge_base_id,
            "count": len(records),
            "documents": [
                _document_record_to_api(record, scoped_account_id, scoped_knowledge_base_id)
                for record in records
            ],
        }

    @app.get("/api/documents/{document_id}/markdown", response_class=PlainTextResponse)
    def get_document_markdown(
        document_id: str,
        account_id: str = DEFAULT_ACCOUNT_ID,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
    ) -> str:
        store = _require_document_store(document_store)
        markdown = store.get_markdown(
            account_id=_scope_id(account_id, DEFAULT_ACCOUNT_ID),
            knowledge_base_id=_scope_id(knowledge_base_id, DEFAULT_KNOWLEDGE_BASE_ID),
            document_id=document_id,
        )
        if markdown is None:
            raise HTTPException(status_code=404, detail="Document markdown is not ready")
        return markdown

    @app.get("/api/documents/{document_id}/pages")
    def get_document_pages(
        document_id: str,
        account_id: str = DEFAULT_ACCOUNT_ID,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
    ) -> dict[str, Any]:
        store = _require_document_store(document_store)
        scoped_account_id = _scope_id(account_id, DEFAULT_ACCOUNT_ID)
        scoped_knowledge_base_id = _scope_id(knowledge_base_id, DEFAULT_KNOWLEDGE_BASE_ID)
        document = store.load_ocr_document(
            account_id=scoped_account_id,
            knowledge_base_id=scoped_knowledge_base_id,
            document_id=document_id,
        )
        if document is None:
            raise HTTPException(status_code=404, detail="OCR pages are not ready")
        return {
            "source_file": document.source_file,
            "total_pages": len(document.pages),
            "pages": [
                {"page_number": page.page_number, "markdown": page.text.strip()}
                for page in document.pages
            ],
        }

    @app.get("/api/documents/{document_id}/download")
    def download_document_markdown(
        document_id: str,
        account_id: str = DEFAULT_ACCOUNT_ID,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
    ):
        store = _require_document_store(document_store)
        scoped_account_id = _scope_id(account_id, DEFAULT_ACCOUNT_ID)
        scoped_knowledge_base_id = _scope_id(knowledge_base_id, DEFAULT_KNOWLEDGE_BASE_ID)
        record = store.get_document(
            account_id=scoped_account_id,
            knowledge_base_id=scoped_knowledge_base_id,
            document_id=document_id,
        )
        markdown = store.get_markdown(
            account_id=scoped_account_id,
            knowledge_base_id=scoped_knowledge_base_id,
            document_id=document_id,
        )
        if markdown is None:
            raise HTTPException(status_code=404, detail="Document markdown is not ready")
        filename = f"{Path(str((record or {}).get('file_name') or document_id)).stem}.md"
        return PlainTextResponse(
            markdown,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
        )

    @app.post("/api/documents/{document_id}/extract-keywords")
    def extract_document_keywords(
        document_id: str,
        account_id: str = Form(DEFAULT_ACCOUNT_ID),
        knowledge_base_id: str = Form(DEFAULT_KNOWLEDGE_BASE_ID),
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
        store = _require_document_store(document_store)
        document = store.load_ocr_document(
            account_id=_scope_id(account_id, DEFAULT_ACCOUNT_ID),
            knowledge_base_id=_scope_id(knowledge_base_id, DEFAULT_KNOWLEDGE_BASE_ID),
            document_id=document_id,
        )
        if document is None:
            raise HTTPException(status_code=404, detail="Document OCR result is not ready")
        return _extract_keywords_response(
            document=document,
            keywords=keywords,
            match_mode=match_mode,
            context_before=context_before,
            context_after=context_after,
            granularity=granularity,
            use_regex=use_regex,
            case_sensitive=case_sensitive,
            normalize_chinese=normalize_chinese,
            deduplicate=deduplicate,
        )

    @app.get("/api/llm/status")
    def get_llm_status() -> dict[str, Any]:
        llm_config = _llm_config(config)
        return {
            "provider": llm_config.get("provider") or "openai_compatible",
            "configured": _llm_config_ready(llm_config),
            "model": llm_config.get("model") or "",
            "temperature": float(llm_config.get("temperature", 0)),
            "max_tokens": int(llm_config.get("max_tokens", 4096)),
        }

    @app.get("/api/llm/conversations")
    def list_llm_conversations(
        account_id: str = DEFAULT_ACCOUNT_ID,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
        limit: int = 100,
    ) -> dict[str, Any]:
        scoped_account_id = _scope_id(account_id, DEFAULT_ACCOUNT_ID)
        scoped_knowledge_base_id = _scope_id(knowledge_base_id, DEFAULT_KNOWLEDGE_BASE_ID)
        with llm_lock:
            conversations = [
                conversation
                for conversation in llm_conversations.values()
                if conversation.account_id == scoped_account_id
                and conversation.knowledge_base_id == scoped_knowledge_base_id
            ]
            conversations.sort(key=lambda conversation: conversation.updated_at, reverse=True)
            limited = conversations[: max(1, min(int(limit), 500))]
            return {
                "account_id": scoped_account_id,
                "knowledge_base_id": scoped_knowledge_base_id,
                "count": len(limited),
                "conversations": [
                    conversation.to_dict(include_messages=False)
                    for conversation in limited
                ],
            }

    @app.post("/api/llm/conversations")
    def create_llm_conversation(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
        conversation = _create_llm_conversation(payload or {}, config)
        with llm_lock:
            llm_conversations[conversation.conversation_id] = conversation
        return conversation.to_dict()

    @app.get("/api/llm/conversations/{conversation_id}")
    def get_llm_conversation(conversation_id: str) -> dict[str, Any]:
        conversation = _get_llm_conversation_or_404(conversation_id, llm_conversations, llm_lock)
        return conversation.to_dict()

    @app.delete("/api/llm/conversations/{conversation_id}")
    def delete_llm_conversation(conversation_id: str) -> dict[str, Any]:
        with llm_lock:
            if conversation_id not in llm_conversations:
                raise HTTPException(status_code=404, detail="LLM conversation does not exist")
            del llm_conversations[conversation_id]
        return {"deleted": True, "conversation_id": conversation_id}

    @app.post("/api/llm/conversations/{conversation_id}/messages")
    def send_llm_message(
        conversation_id: str,
        payload: dict[str, Any] | None = Body(default=None),
    ) -> dict[str, Any]:
        payload = payload or {}
        content = _payload_text(payload, "content", "message")
        if not content:
            raise HTTPException(status_code=400, detail="Message content is required")
        options = payload.get("options")
        if options is not None and not isinstance(options, dict):
            raise HTTPException(status_code=400, detail="options must be an object")

        with llm_lock:
            conversation = llm_conversations.get(conversation_id)
            if conversation is None:
                raise HTTPException(status_code=404, detail="LLM conversation does not exist")
            llm_messages = _conversation_messages_for_llm(conversation, content)

        try:
            assistant_content = _create_llm_client(config).chat(llm_messages, options=options)
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        user_message = ChatMessage(role="user", content=content)
        assistant_message = ChatMessage(role="assistant", content=assistant_content)
        with llm_lock:
            conversation = llm_conversations.get(conversation_id)
            if conversation is None:
                raise HTTPException(status_code=404, detail="LLM conversation does not exist")
            conversation.messages.extend([user_message, assistant_message])
            if conversation.title == _default_llm_title():
                conversation.title = _conversation_title(content)
            conversation.updated_at = _now()
            return {
                "conversation": conversation.to_dict(),
                "user_message": user_message.to_dict(),
                "assistant_message": assistant_message.to_dict(),
            }

    @app.post("/api/jobs")
    async def create_job(
        file: UploadFile = File(...),
        engine: str = Form(DEFAULT_ENGINE),
        account_id: str = Form(DEFAULT_ACCOUNT_ID),
        knowledge_base_id: str = Form(DEFAULT_KNOWLEDGE_BASE_ID),
    ) -> dict[str, Any]:
        selected_engine = _normalize_engine(engine)
        if not _is_pdf_upload(file):
            raise HTTPException(status_code=400, detail="只支持 PDF 文件")
        job = await _create_web_job_from_upload(
            file,
            selected_engine,
            web_root,
            upload_root,
            account_id=_scope_id(account_id, DEFAULT_ACCOUNT_ID),
            knowledge_base_id=_scope_id(knowledge_base_id, DEFAULT_KNOWLEDGE_BASE_ID),
        )
        with jobs_lock:
            jobs[job.job_id] = job

        thread = threading.Thread(
            target=_run_job,
            args=(job.job_id, jobs, jobs_lock, ocr_lock, config, document_store),
            daemon=True,
        )
        thread.start()
        return job.to_dict()

    @app.post("/api/jobs/batch")
    async def create_batch_jobs(
        files: list[UploadFile] = File(...),
        engine: str = Form(DEFAULT_ENGINE),
        account_id: str = Form(DEFAULT_ACCOUNT_ID),
        knowledge_base_id: str = Form(DEFAULT_KNOWLEDGE_BASE_ID),
    ) -> dict[str, Any]:
        selected_engine = _normalize_engine(engine)
        pdf_files = [upload for upload in files if _is_pdf_upload(upload)]
        if not pdf_files:
            raise HTTPException(status_code=400, detail="请选择至少一个 PDF 文件")

        batch_id = uuid.uuid4().hex[:12]
        created_jobs: list[WebJob] = []
        queue_total = len(pdf_files)
        for index, upload in enumerate(pdf_files, start=1):
            created_jobs.append(
                await _create_web_job_from_upload(
                    upload,
                    selected_engine,
                    web_root,
                    upload_root,
                    account_id=_scope_id(account_id, DEFAULT_ACCOUNT_ID),
                    knowledge_base_id=_scope_id(knowledge_base_id, DEFAULT_KNOWLEDGE_BASE_ID),
                    batch_id=batch_id,
                    queue_index=index,
                    queue_total=queue_total,
                )
            )

        with jobs_lock:
            for job in created_jobs:
                jobs[job.job_id] = job

        thread = threading.Thread(
            target=_run_batch_jobs,
            args=([job.job_id for job in created_jobs], jobs, jobs_lock, ocr_lock, config, document_store),
            daemon=True,
        )
        thread.start()

        return {
            "batch_id": batch_id,
            "count": len(created_jobs),
            "skipped": len(files) - len(pdf_files),
            "jobs": [job.to_dict() for job in created_jobs],
        }

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        job = _get_job_or_404(job_id, jobs, jobs_lock)
        data = job.to_dict()
        data["log_tail"] = _read_log_tail(job.log_path)
        return data

    @app.get("/api/jobs/{job_id}/markdown", response_class=PlainTextResponse)
    def get_markdown(job_id: str) -> str:
        job = _get_job_or_404(job_id, jobs, jobs_lock)
        if job.state != "done":
            raise HTTPException(status_code=404, detail="Markdown 还没有生成")
        if document_store is not None and job.document_id:
            markdown = document_store.get_markdown(
                account_id=job.account_id,
                knowledge_base_id=job.knowledge_base_id,
                document_id=job.document_id,
            )
            if markdown is not None:
                return markdown
        if not job.markdown_path or not job.markdown_path.exists():
            raise HTTPException(status_code=404, detail="Markdown 还没有生成")
        return job.markdown_path.read_text(encoding="utf-8")

    @app.get("/api/jobs/{job_id}/pages")
    def get_pages(job_id: str) -> dict[str, Any]:
        job = _get_job_or_404(job_id, jobs, jobs_lock)
        if job.state != "done":
            raise HTTPException(status_code=404, detail="OCR pages are not ready")
        document = job.ocr_document
        if document is None and document_store is not None and job.document_id:
            document = document_store.load_ocr_document(
                account_id=job.account_id,
                knowledge_base_id=job.knowledge_base_id,
                document_id=job.document_id,
            )
            if document is not None:
                _update_job(jobs, jobs_lock, job_id, ocr_document=document)
        if document is None:
            raise HTTPException(status_code=404, detail="OCR pages are not ready")
        return {
            "source_file": document.source_file,
            "total_pages": len(document.pages),
            "pages": [
                {"page_number": page.page_number, "markdown": page.text.strip()}
                for page in document.pages
            ],
        }

    @app.get("/api/jobs/{job_id}/download")
    def download_markdown(job_id: str):
        job = _get_job_or_404(job_id, jobs, jobs_lock)
        if job.state != "done":
            raise HTTPException(status_code=404, detail="Markdown 还没有生成")
        if job.markdown_path and job.markdown_path.exists():
            return FileResponse(
                path=job.markdown_path,
                media_type="text/markdown; charset=utf-8",
                filename=job.markdown_path.name,
            )
        if document_store is not None and job.document_id:
            markdown = document_store.get_markdown(
                account_id=job.account_id,
                knowledge_base_id=job.knowledge_base_id,
                document_id=job.document_id,
            )
            if markdown is not None:
                filename = f"{Path(job.file_name).stem}.md"
                return PlainTextResponse(
                    markdown,
                    media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
                )
        raise HTTPException(status_code=404, detail="Markdown 还没有生成")

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
        if job.state == "done" and job.ocr_document is None and document_store is not None and job.document_id:
            document = document_store.load_ocr_document(
                account_id=job.account_id,
                knowledge_base_id=job.knowledge_base_id,
                document_id=job.document_id,
            )
            if document is not None:
                _update_job(jobs, jobs_lock, job_id, ocr_document=document)
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


async def _create_web_job_from_upload(
    file: UploadFile,
    selected_engine: str,
    web_root: Path,
    upload_root: Path,
    *,
    account_id: str = DEFAULT_ACCOUNT_ID,
    knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
    batch_id: str | None = None,
    queue_index: int | None = None,
    queue_total: int | None = None,
) -> WebJob:
    original_filename = Path(file.filename or "").name
    filename = _safe_pdf_name(original_filename)

    job_id = uuid.uuid4().hex[:12]
    job_dir = web_root / job_id
    upload_dir = upload_root / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    upload_dir.mkdir(parents=True, exist_ok=True)
    input_path = upload_dir / filename

    digest = hashlib.sha256()
    with input_path.open("wb") as f:
        while chunk := await file.read(1024 * 1024):
            digest.update(chunk)
            f.write(chunk)

    now = _now()
    return WebJob(
        job_id=job_id,
        batch_id=batch_id,
        queue_index=queue_index,
        queue_total=queue_total,
        file_name=filename,
        engine=selected_engine,
        job_dir=job_dir,
        input_path=input_path,
        log_path=job_dir / "ocr_run.log",
        account_id=_scope_id(account_id, DEFAULT_ACCOUNT_ID),
        knowledge_base_id=_scope_id(knowledge_base_id, DEFAULT_KNOWLEDGE_BASE_ID),
        file_sha256=digest.hexdigest(),
        created_at=now,
        updated_at=now,
    )


def _is_pdf_upload(file: UploadFile) -> bool:
    return str(file.filename or "").lower().endswith(".pdf")


def serve(config: dict[str, Any], output_dir: str | Path, host: str = "127.0.0.1", port: int = 8010) -> None:
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
    parser.add_argument("--port", type=int, default=8010, help="Web server port.")
    args = parser.parse_args()

    serve(config=load_config(args.config), output_dir=args.output, host=args.host, port=args.port)


def _run_job(
    job_id: str,
    jobs: dict[str, WebJob],
    jobs_lock: threading.Lock,
    ocr_lock: threading.Lock,
    config: dict[str, Any],
    document_store: ElasticsearchDocumentStore | None,
) -> None:
    _update_job(jobs, jobs_lock, job_id, state="queued", progress=1, message="排队等待 OCR 任务")
    with ocr_lock:
        job = _get_job(job_id, jobs, jobs_lock)
        if not job:
            return
        set_log_file(job.log_path)
        try:
            _recognize_job(job_id, jobs, jobs_lock, config, document_store)
        except Exception as exc:  # pragma: no cover - depends on external OCR service
            log(f"Web OCR failed: {exc}")
            _update_job(jobs, jobs_lock, job_id, state="failed", progress=100, message="处理失败", error=str(exc))
            _save_job_snapshot(document_store, _get_job(job_id, jobs, jobs_lock), finished=True)
        finally:
            set_log_file(None)


def _run_batch_jobs(
    job_ids: list[str],
    jobs: dict[str, WebJob],
    jobs_lock: threading.Lock,
    ocr_lock: threading.Lock,
    config: dict[str, Any],
    document_store: ElasticsearchDocumentStore | None,
) -> None:
    for job_id in job_ids:
        _update_job(jobs, jobs_lock, job_id, state="queued", progress=1, message="排队等待 OCR 任务")

    with ocr_lock:
        for job_id in job_ids:
            job = _get_job(job_id, jobs, jobs_lock)
            if not job:
                continue
            set_log_file(job.log_path)
            try:
                if job.queue_index and job.queue_total:
                    _update_job(
                        jobs,
                        jobs_lock,
                        job_id,
                        message=f"队列第 {job.queue_index}/{job.queue_total} 个任务开始处理",
                    )
                _recognize_job(job_id, jobs, jobs_lock, config, document_store)
            except Exception as exc:  # pragma: no cover - depends on external OCR service
                log(f"Web OCR failed: {exc}")
                _update_job(jobs, jobs_lock, job_id, state="failed", progress=100, message="处理失败", error=str(exc))
                _save_job_snapshot(document_store, _get_job(job_id, jobs, jobs_lock), finished=True)
            finally:
                set_log_file(None)


def _recognize_job(
    job_id: str,
    jobs: dict[str, WebJob],
    jobs_lock: threading.Lock,
    config: dict[str, Any],
    document_store: ElasticsearchDocumentStore | None = None,
) -> None:
    job = _get_job(job_id, jobs, jobs_lock)
    if not job:
        return

    ocr_config = _web_ocr_config(config, job.engine)
    options = ocr_config.get("options", {})
    max_pages = int(options.get("max_pages_per_file", 200))
    engine_label = ENGINE_LABELS.get(job.engine, job.engine)
    file_sha256 = job.file_sha256 or compute_file_sha256(job.input_path)
    ocr_options_hash = options_hash(options)
    processing_fingerprint = build_processing_fingerprint(file_sha256, job.engine, options)
    document_id = job.document_id or uuid.uuid4().hex
    _update_job(
        jobs,
        jobs_lock,
        job_id,
        document_id=document_id,
        file_sha256=file_sha256,
        processing_fingerprint=processing_fingerprint,
    )

    if document_store is not None:
        cached_document = document_store.find_processed_document(
            account_id=job.account_id,
            knowledge_base_id=job.knowledge_base_id,
            file_sha256=file_sha256,
            processing_fingerprint=processing_fingerprint,
        )
        if cached_document:
            _complete_job_from_cached_document(job_id, jobs, jobs_lock, document_store, cached_document)
            return
        _save_processing_document(
            document_store=document_store,
            job=_get_job(job_id, jobs, jobs_lock) or job,
            file_size=job.input_path.stat().st_size,
            ocr_options_hash=ocr_options_hash,
        )
        _save_job_snapshot(document_store, _get_job(job_id, jobs, jobs_lock))

    _update_job(jobs, jobs_lock, job_id, state="running", progress=3, message="读取 PDF 页数")
    client = create_ocr_client(ocr_config)
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
    markdown_text = markdown_path.read_text(encoding="utf-8")
    if document_store is not None:
        document_store.save_ocr_result(
            account_id=job.account_id,
            knowledge_base_id=job.knowledge_base_id,
            document_id=document_id,
            file_name=job.file_name,
            source_path=str(job.input_path),
            file_size=job.input_path.stat().st_size,
            file_sha256=file_sha256,
            processing_fingerprint=processing_fingerprint,
            ocr_options_hash=ocr_options_hash,
            document=document,
            markdown=markdown_text,
            chunk_pages=int(config.get("extraction", {}).get("chunk_pages", 3)),
            metadata={"web_job_id": job.job_id},
        )
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
    _save_job_snapshot(document_store, _get_job(job_id, jobs, jobs_lock), finished=True)


def _complete_job_from_cached_document(
    job_id: str,
    jobs: dict[str, WebJob],
    jobs_lock: threading.Lock,
    document_store: ElasticsearchDocumentStore,
    cached_document: dict[str, Any],
) -> None:
    job = _get_job(job_id, jobs, jobs_lock)
    if not job:
        return

    document_id = str(cached_document.get("document_id") or "")
    document = document_store.load_ocr_document(
        account_id=job.account_id,
        knowledge_base_id=job.knowledge_base_id,
        document_id=document_id,
    )
    markdown = document_store.get_markdown(
        account_id=job.account_id,
        knowledge_base_id=job.knowledge_base_id,
        document_id=document_id,
    )
    markdown_path = job.job_dir / f"{Path(job.file_name).stem}.md"
    if markdown:
        markdown_path.write_text(markdown, encoding="utf-8")
    elif document:
        write_ocr_markdown(document, markdown_path)
    else:
        markdown_path = None  # type: ignore[assignment]

    total_pages = cached_document.get("page_count")
    if not isinstance(total_pages, int) and document:
        total_pages = len(document.pages)
    _update_job(
        jobs,
        jobs_lock,
        job_id,
        state="done",
        progress=100,
        message="已处理过，直接复用结果",
        document_id=document_id,
        total_pages=total_pages if isinstance(total_pages, int) else None,
        markdown_path=markdown_path,
        ocr_document=document,
        reused=True,
    )
    log(f"Web OCR reused cached document: job={job_id}; document_id={document_id}.")
    _save_job_snapshot(document_store, _get_job(job_id, jobs, jobs_lock), finished=True)


def _save_processing_document(
    *,
    document_store: ElasticsearchDocumentStore,
    job: WebJob,
    file_size: int,
    ocr_options_hash: str,
) -> None:
    if not job.document_id or not job.file_sha256 or not job.processing_fingerprint:
        return
    now = _es_now()
    document_store.save_processing_document(
        {
            "account_id": job.account_id,
            "knowledge_base_id": job.knowledge_base_id,
            "document_id": job.document_id,
            "file_name": job.file_name,
            "file_ext": Path(job.file_name).suffix.lower().lstrip("."),
            "mime_type": "application/pdf",
            "file_size": file_size,
            "file_sha256": job.file_sha256,
            "status": "processing",
            "source": "web_upload",
            "source_path": str(job.input_path),
            "ocr_engine": job.engine,
            "ocr_options_hash": ocr_options_hash,
            "pipeline_version": PIPELINE_VERSION,
            "processing_fingerprint": job.processing_fingerprint,
            "page_count": None,
            "language": "",
            "title": "",
            "tags": [],
            "metadata": {"web_job_id": job.job_id},
            "markdown": "",
            "ocr_json": {},
            "created_at": now,
            "updated_at": now,
        }
    )


def _save_job_snapshot(
    document_store: ElasticsearchDocumentStore | None,
    job: WebJob | None,
    *,
    finished: bool = False,
) -> None:
    if document_store is None or job is None:
        return
    try:
        now = _es_now()
        document_store.save_job(
            {
                "account_id": job.account_id,
                "knowledge_base_id": job.knowledge_base_id,
                "job_id": job.job_id,
                "document_id": job.document_id,
                "file_name": job.file_name,
                "type": "ocr",
                "state": job.state,
                "progress": job.progress,
                "message": job.message,
                "error": job.error,
                "engine": job.engine,
                "reused": job.reused,
                "created_at": now,
                "updated_at": now,
                "finished_at": now if finished else None,
            }
        )
    except Exception as exc:  # pragma: no cover - depends on external Elasticsearch
        log(f"Failed to save web job snapshot to Elasticsearch: {exc}")


def _require_document_store(document_store: ElasticsearchDocumentStore | None) -> ElasticsearchDocumentStore:
    if document_store is None:
        raise HTTPException(status_code=503, detail="Elasticsearch document store is not enabled")
    return document_store


def _document_record_to_api(
    record: dict[str, Any],
    account_id: str,
    knowledge_base_id: str,
) -> dict[str, Any]:
    document_id = str(record.get("document_id") or "")
    query = f"account_id={quote(account_id)}&knowledge_base_id={quote(knowledge_base_id)}"
    return {
        "account_id": account_id,
        "knowledge_base_id": knowledge_base_id,
        "document_id": document_id,
        "file_name": record.get("file_name") or "",
        "file_ext": record.get("file_ext") or "",
        "file_size": record.get("file_size"),
        "file_sha256": record.get("file_sha256"),
        "status": record.get("status") or "",
        "ocr_engine": record.get("ocr_engine") or "",
        "page_count": record.get("page_count"),
        "processed_at": record.get("processed_at"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "markdown_url": f"/api/documents/{quote(document_id)}/markdown?{query}",
        "download_url": f"/api/documents/{quote(document_id)}/download?{query}",
        "pages_url": f"/api/documents/{quote(document_id)}/pages?{query}",
    }


def _extract_keywords_response(
    *,
    document: OcrDocument,
    keywords: str,
    match_mode: str,
    context_before: int,
    context_after: int,
    granularity: str,
    use_regex: bool,
    case_sensitive: bool,
    normalize_chinese: bool,
    deduplicate: bool,
) -> dict[str, Any]:
    parsed_keywords = _parse_keywords(keywords)
    if not parsed_keywords:
        raise HTTPException(status_code=400, detail="璇疯嚦灏戣緭鍏ヤ竴涓叧閿瘝")
    normalized_mode = str(match_mode or "any").lower()
    if normalized_mode not in {"any", "all"}:
        raise HTTPException(status_code=400, detail="match_mode 鍙敮鎸?any 鎴?all")
    normalized_granularity = str(granularity or "paragraph").lower()
    if normalized_granularity not in {"paragraph", "page"}:
        raise HTTPException(status_code=400, detail="granularity 鍙敮鎸?paragraph 鎴?page")

    results = extract_keywords(
        document=document,
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


def _llm_config(config: dict[str, Any]) -> dict[str, Any]:
    value = config.get("llm", {})
    return value if isinstance(value, dict) else {}


def _llm_config_ready(config: dict[str, Any]) -> bool:
    return bool(config.get("api_base_url") and config.get("api_key") and config.get("model"))


def _create_llm_client(config: dict[str, Any]) -> OpenAICompatibleClient:
    llm_config = _llm_config(config)
    provider = str(llm_config.get("provider") or "openai_compatible").strip().lower()
    if provider not in {"openai_compatible", "openai-compatible", "openai"}:
        raise HTTPException(status_code=400, detail=f"Unsupported LLM provider: {provider}")
    return OpenAICompatibleClient.from_config(llm_config)


def _create_llm_conversation(payload: dict[str, Any], config: dict[str, Any]) -> LlmConversation:
    llm_config = _llm_config(config)
    title = _payload_text(payload, "title") or _default_llm_title()
    system_prompt = _payload_text(payload, "system_prompt") or str(llm_config.get("system_prompt") or "").strip()
    conversation = LlmConversation(
        conversation_id=uuid.uuid4().hex[:12],
        title=title,
        account_id=_scope_id(payload.get("account_id"), DEFAULT_ACCOUNT_ID),
        knowledge_base_id=_scope_id(payload.get("knowledge_base_id"), DEFAULT_KNOWLEDGE_BASE_ID),
        system_prompt=system_prompt,
    )

    raw_messages = payload.get("messages")
    if raw_messages is not None:
        if not isinstance(raw_messages, list):
            raise HTTPException(status_code=400, detail="messages must be an array")
        for raw_message in raw_messages:
            if not isinstance(raw_message, dict):
                raise HTTPException(status_code=400, detail="Each message must be an object")
            role = str(raw_message.get("role") or "").strip().lower()
            content = _payload_text(raw_message, "content")
            if not content:
                continue
            if role == "system":
                if not conversation.system_prompt:
                    conversation.system_prompt = content
                continue
            if role not in {"user", "assistant"}:
                raise HTTPException(status_code=400, detail=f"Unsupported message role: {role}")
            conversation.messages.append(ChatMessage(role=role, content=content))
        if conversation.messages:
            conversation.updated_at = _now()
    return conversation


def _conversation_messages_for_llm(conversation: LlmConversation, user_content: str) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if conversation.system_prompt.strip():
        messages.append({"role": "system", "content": conversation.system_prompt.strip()})
    for message in conversation.messages:
        if message.role in {"user", "assistant"} and message.content.strip():
            messages.append({"role": message.role, "content": message.content.strip()})
    messages.append({"role": "user", "content": user_content.strip()})
    return messages


def _get_llm_conversation_or_404(
    conversation_id: str,
    conversations: dict[str, LlmConversation],
    conversations_lock: threading.Lock,
) -> LlmConversation:
    with conversations_lock:
        conversation = conversations.get(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="LLM conversation does not exist")
        return conversation


def _payload_text(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _conversation_title(content: str, max_length: int = 32) -> str:
    title = re.sub(r"\s+", " ", content).strip()
    if not title:
        return _default_llm_title()
    return title if len(title) <= max_length else f"{title[:max_length].rstrip()}..."


def _default_llm_title() -> str:
    return "New chat"


def _web_ocr_config(config: dict[str, Any], engine: str) -> dict[str, Any]:
    ocr_config = copy.deepcopy(config.get("ocr", {}))
    engine_configs = copy.deepcopy(config.get("ocr_engines", {}))
    current_engine = _normalize_engine(ocr_config.get("engine", DEFAULT_ENGINE))

    if engine in engine_configs and isinstance(engine_configs[engine], dict):
        merged = copy.deepcopy(ocr_config)
        merged.update(engine_configs[engine])
        ocr_config = merged
    elif current_engine != engine:
        # When switching engines in the UI, prefer environment variables over the
        # single CLI-oriented config block so both providers can coexist.
        ocr_config["api_base_url"] = ""
        ocr_config["api_token"] = ""
        ocr_config["options"] = {}

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
        options.setdefault("max_pages_per_file", 50)
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
    normalized = ENGINE_ALIASES.get(str(engine or DEFAULT_ENGINE).strip().lower())
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


def _scope_id(value: Any, fallback: str) -> str:
    normalized = str(value or "").strip()
    return normalized or fallback


def _es_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")



if __name__ == "__main__":
    main()
