from __future__ import annotations

import copy
import base64
import binascii
import hashlib
import hmac
import json
import math
import os
import re
import time
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ocean.config import load_config
from ocean.exporters import write_ocr_markdown
from ocean.extractors import extract_keywords
from ocean.llm.client import OpenAICompatibleClient
from ocean.logging_utils import log, set_log_file
from ocean.models import ExtractionResult, OcrDocument
from ocean.ocr import create_ocr_client
from ocean.pdf_utils import count_pdf_pages
from ocean.pipeline import _fallback_ocr_config, _recognize_with_fallback
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
    from fastapi import Body, FastAPI, File, Form, Header, HTTPException, UploadFile
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, StreamingResponse
except ImportError:  # pragma: no cover
    Body = FastAPI = File = Form = Header = HTTPException = UploadFile = None  # type: ignore[assignment]
    StaticFiles = None  # type: ignore[assignment]
    FileResponse = HTMLResponse = PlainTextResponse = StreamingResponse = None  # type: ignore[assignment]


ENGINE_LABELS = {
    "paddleocr": "PaddleOCR",
    "mineru": "MinerU",
}
DEFAULT_ENGINE = "mineru"
DEFAULT_MAX_CONTEXT_DOCUMENTS = 5
DEFAULT_MAX_DOCUMENT_CONTEXT_CHARS = 30000
DEFAULT_MAX_HISTORY_MESSAGES = 20
LLM_CONTEXT_MODE_NONE = "none"
LLM_CONTEXT_MODE_DOCUMENTS = "documents"
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
    origin: str = "global"
    system_prompt: str = ""
    context_mode: str = LLM_CONTEXT_MODE_NONE
    context_documents: list[dict[str, Any]] = field(default_factory=list)
    messages: list[ChatMessage] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: _now())
    updated_at: str = field(default_factory=lambda: _now())

    def to_dict(self, *, include_messages: bool = True) -> dict[str, Any]:
        context_documents = [dict(document) for document in self.context_documents]
        data: dict[str, Any] = {
            "conversation_id": self.conversation_id,
            "account_id": self.account_id,
            "knowledge_base_id": self.knowledge_base_id,
            "title": self.title,
            "origin": self.origin,
            "system_prompt": self.system_prompt,
            "context_mode": self.context_mode,
            "context_document_ids": [
                str(document.get("document_id") or "")
                for document in context_documents
                if str(document.get("document_id") or "").strip()
            ],
            "context_documents": context_documents,
            "message_count": len(self.messages),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if include_messages:
            data["messages"] = [message.to_dict() for message in self.messages]
        return data


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    username: str
    account_id: str
    role: str = "user"
    display_name: str = ""
    authenticated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "account_id": self.account_id,
            "role": self.role,
            "display_name": self.display_name,
        }


def make_app(config: dict[str, Any], output_dir: str | Path = "./outputs"):
    if Body is None or FastAPI is None or File is None or Form is None or HTTPException is None or UploadFile is None:
        raise RuntimeError("Web UI dependencies are missing. Run: pip install -e .")

    app = FastAPI(title="OCR Assistant Web")
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
    auth_config = _auth_config(config)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        if not frontend_index.exists():
            raise HTTPException(
                status_code=503,
                detail="Frontend build is missing. Run npm run build in the frontend directory.",
            )
        return frontend_index.read_text(encoding="utf-8")

    @app.post("/api/auth/login")
    def login(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
        payload = payload or {}
        username = _payload_text(payload, "username")
        password = str(payload.get("password") or "")
        if _auth_enabled(auth_config):
            user_config = _find_auth_user(auth_config, username)
            if not user_config or not _verify_password(password, str(user_config.get("password_hash") or "")):
                raise HTTPException(status_code=401, detail="Invalid username or password")
            user = _user_from_config(user_config)
        else:
            if username != DEFAULT_ACCOUNT_ID or password != DEFAULT_ACCOUNT_ID:
                raise HTTPException(status_code=401, detail="Invalid username or password")
            user = _local_mode_user()
        token = _encode_token(user, auth_config)
        return {
            "access_token": token,
            "token_type": "bearer",
            "auth_enabled": _auth_enabled(auth_config),
            "user": user.to_dict(),
        }

    @app.get("/api/auth/me")
    def get_current_user(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        user = _current_user(auth_config, authorization)
        return {
            "auth_enabled": _auth_enabled(auth_config),
            "user": user.to_dict(),
        }

    @app.post("/api/auth/logout")
    def logout() -> dict[str, Any]:
        return {"ok": True}

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
        account_id: str | None = None,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
        q: str = "",
        limit: int = 100,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        store = _require_document_store(document_store)
        current_user = _current_user(auth_config, authorization)
        scoped_account_id = _authorized_account_id(current_user, account_id)
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
        account_id: str | None = None,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
        authorization: str | None = Header(default=None),
    ) -> str:
        store = _require_document_store(document_store)
        current_user = _current_user(auth_config, authorization)
        markdown = store.get_markdown(
            account_id=_authorized_account_id(current_user, account_id),
            knowledge_base_id=_scope_id(knowledge_base_id, DEFAULT_KNOWLEDGE_BASE_ID),
            document_id=document_id,
        )
        if markdown is None:
            raise HTTPException(status_code=404, detail="Document markdown is not ready")
        return markdown

    @app.get("/api/documents/{document_id}/pages")
    def get_document_pages(
        document_id: str,
        account_id: str | None = None,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        store = _require_document_store(document_store)
        current_user = _current_user(auth_config, authorization)
        scoped_account_id = _authorized_account_id(current_user, account_id)
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
        account_id: str | None = None,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
        authorization: str | None = Header(default=None),
    ):
        store = _require_document_store(document_store)
        current_user = _current_user(auth_config, authorization)
        scoped_account_id = _authorized_account_id(current_user, account_id)
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
        account_id: str | None = Form(default=None),
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
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        store = _require_document_store(document_store)
        current_user = _current_user(auth_config, authorization)
        document = store.load_ocr_document(
            account_id=_authorized_account_id(current_user, account_id),
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
            "web_search_enabled": _llm_web_search_options(config) is not None,
        }

    @app.get("/api/llm/conversations")
    def list_llm_conversations(
        account_id: str | None = None,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
        document_id: str = "",
        context_mode: str = "",
        limit: int = 100,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        current_user = _current_user(auth_config, authorization)
        scoped_account_id = _authorized_account_id(current_user, account_id)
        scoped_knowledge_base_id = _scope_id(knowledge_base_id, DEFAULT_KNOWLEDGE_BASE_ID)
        scoped_document_id = str(document_id or "").strip()
        scoped_context_mode = _normalize_llm_context_mode_filter(context_mode)
        if _llm_store_enabled(document_store):
            records = document_store.list_llm_conversations(
                {
                    "account_id": scoped_account_id,
                    "knowledge_base_id": scoped_knowledge_base_id,
                    "document_id": scoped_document_id,
                    "context_mode": scoped_context_mode,
                    "limit": limit,
                }
            )
            return {
                "account_id": scoped_account_id,
                "knowledge_base_id": scoped_knowledge_base_id,
                "count": len(records),
                "conversations": [_llm_record_to_api(record, include_messages=False) for record in records],
            }

        with llm_lock:
            conversations = [
                conversation
                for conversation in llm_conversations.values()
                if _llm_conversation_matches(
                    conversation,
                    account_id=scoped_account_id,
                    knowledge_base_id=scoped_knowledge_base_id,
                    document_id=scoped_document_id,
                    context_mode=scoped_context_mode,
                )
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
    def create_llm_conversation(
        payload: dict[str, Any] | None = Body(default=None),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        current_user = _current_user(auth_config, authorization)
        payload = payload or {}
        conversation = _create_llm_conversation(
            payload,
            config,
            account_id=_authorized_account_id(current_user, payload.get("account_id")),
            document_store=document_store,
        )
        if _llm_store_enabled(document_store):
            conversation_record = _llm_conversation_record(conversation, config)
            if conversation.messages:
                conversation_record["message_count"] = 0
            saved = document_store.save_llm_conversation(conversation_record)
            if conversation.messages:
                now = _es_now()
                document_store.append_llm_messages(
                    {
                        "conversation_id": conversation.conversation_id,
                        "account_id": conversation.account_id,
                        "knowledge_base_id": conversation.knowledge_base_id,
                        "messages": [
                            {
                                "message_id": message.message_id,
                                "role": message.role,
                                "content": message.content,
                                "created_at": now,
                            }
                            for message in conversation.messages
                        ],
                        "start_sequence": 1,
                        "message_count": len(conversation.messages),
                    }
                )
                saved = document_store.get_llm_conversation(
                    {
                        "conversation_id": conversation.conversation_id,
                        "account_id": conversation.account_id,
                        "knowledge_base_id": conversation.knowledge_base_id,
                        "include_messages": True,
                    }
                ) or saved
            return _llm_record_to_api(saved)

        with llm_lock:
            llm_conversations[conversation.conversation_id] = conversation
        return conversation.to_dict()

    @app.get("/api/llm/conversations/{conversation_id}")
    def get_llm_conversation(
        conversation_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        current_user = _current_user(auth_config, authorization)
        if _llm_store_enabled(document_store):
            record = document_store.get_llm_conversation(
                {
                    "conversation_id": conversation_id,
                    "account_id": current_user.account_id,
                    "include_messages": True,
                }
            )
            if record is None:
                raise HTTPException(status_code=404, detail="LLM conversation does not exist")
            return _llm_record_to_api(record)

        conversation = _get_llm_conversation_or_404(conversation_id, llm_conversations, llm_lock)
        _authorize_account_match(current_user, conversation.account_id)
        return conversation.to_dict()

    @app.delete("/api/llm/conversations/{conversation_id}")
    def delete_llm_conversation(
        conversation_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        current_user = _current_user(auth_config, authorization)
        if _llm_store_enabled(document_store):
            deleted = document_store.soft_delete_llm_conversation(
                {
                    "conversation_id": conversation_id,
                    "account_id": current_user.account_id,
                }
            )
            if deleted is None:
                raise HTTPException(status_code=404, detail="LLM conversation does not exist")
            return {"deleted": True, "conversation_id": conversation_id}

        with llm_lock:
            conversation = llm_conversations.get(conversation_id)
            if conversation is None:
                raise HTTPException(status_code=404, detail="LLM conversation does not exist")
            _authorize_account_match(current_user, conversation.account_id)
            del llm_conversations[conversation_id]
        return {"deleted": True, "conversation_id": conversation_id}

    @app.post("/api/llm/conversations/{conversation_id}/messages")
    def send_llm_message(
        conversation_id: str,
        payload: dict[str, Any] | None = Body(default=None),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        current_user = _current_user(auth_config, authorization)
        payload = payload or {}
        content = _payload_text(payload, "content", "message")
        if not content:
            raise HTTPException(status_code=400, detail="Message content is required")
        options = payload.get("options")
        if options is not None and not isinstance(options, dict):
            raise HTTPException(status_code=400, detail="options must be an object")

        if _llm_store_enabled(document_store):
            record = document_store.get_llm_conversation(
                {
                    "conversation_id": conversation_id,
                    "account_id": current_user.account_id,
                    "include_messages": True,
                }
            )
            if record is None:
                raise HTTPException(status_code=404, detail="LLM conversation does not exist")
            conversation_snapshot = _llm_conversation_from_record(record)
        else:
            with llm_lock:
                conversation = llm_conversations.get(conversation_id)
                if conversation is None:
                    raise HTTPException(status_code=404, detail="LLM conversation does not exist")
                _authorize_account_match(current_user, conversation.account_id)
                conversation_snapshot = copy.deepcopy(conversation)

        conversation_snapshot = _conversation_with_context_update(
            conversation_snapshot,
            payload,
            config=config,
            document_store=document_store,
        )
        llm_messages = _conversation_messages_for_llm(
            conversation_snapshot,
            content,
            config=config,
            document_store=document_store,
        )

        try:
            assistant_content = _create_llm_client(config).chat(
                llm_messages,
                options=_llm_chat_options(config, options),
            )
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        user_message = ChatMessage(role="user", content=content)
        assistant_message = ChatMessage(role="assistant", content=assistant_content)
        if _llm_store_enabled(document_store):
            now = _es_now()
            title_update = (
                _conversation_title(content)
                if _is_default_llm_title(conversation_snapshot.title)
                else conversation_snapshot.title
            )
            conversation_updates = {
                "title": title_update,
                "context_mode": conversation_snapshot.context_mode,
                "context_document_ids": [
                    str(document.get("document_id") or "")
                    for document in conversation_snapshot.context_documents
                    if str(document.get("document_id") or "").strip()
                ],
                "context_documents": [dict(document) for document in conversation_snapshot.context_documents],
            }
            document_store.append_llm_messages(
                {
                    "conversation_id": conversation_snapshot.conversation_id,
                    "account_id": conversation_snapshot.account_id,
                    "knowledge_base_id": conversation_snapshot.knowledge_base_id,
                    "messages": [
                        {
                            "message_id": user_message.message_id,
                            "role": user_message.role,
                            "content": user_message.content,
                            "created_at": now,
                        },
                        {
                            "message_id": assistant_message.message_id,
                            "role": assistant_message.role,
                            "content": assistant_message.content,
                            "created_at": now,
                        },
                    ],
                    "conversation_updates": conversation_updates,
                }
            )
            updated = document_store.get_llm_conversation(
                {
                    "conversation_id": conversation_snapshot.conversation_id,
                    "account_id": conversation_snapshot.account_id,
                    "knowledge_base_id": conversation_snapshot.knowledge_base_id,
                    "include_messages": True,
                }
            )
            if updated is None:
                raise HTTPException(status_code=404, detail="LLM conversation does not exist")
            return {
                "conversation": _llm_api_with_appended_messages(
                    updated,
                    conversation_snapshot,
                    user_message,
                    assistant_message,
                ),
                "user_message": user_message.to_dict(),
                "assistant_message": assistant_message.to_dict(),
            }

        with llm_lock:
            conversation = llm_conversations.get(conversation_id)
            if conversation is None:
                raise HTTPException(status_code=404, detail="LLM conversation does not exist")
            _authorize_account_match(current_user, conversation.account_id)
            conversation.context_documents = [dict(document) for document in conversation_snapshot.context_documents]
            conversation.context_mode = conversation_snapshot.context_mode
            conversation.messages.extend([user_message, assistant_message])
            if _is_default_llm_title(conversation.title):
                conversation.title = _conversation_title(content)
            conversation.updated_at = _now()
            return {
                "conversation": conversation.to_dict(),
                "user_message": user_message.to_dict(),
                "assistant_message": assistant_message.to_dict(),
            }

    @app.post("/api/llm/conversations/{conversation_id}/messages/stream")
    def stream_llm_message(
        conversation_id: str,
        payload: dict[str, Any] | None = Body(default=None),
        authorization: str | None = Header(default=None),
    ) -> StreamingResponse:
        current_user = _current_user(auth_config, authorization)
        payload = payload or {}
        content = _payload_text(payload, "content", "message")
        if not content:
            raise HTTPException(status_code=400, detail="Message content is required")
        options = payload.get("options")
        if options is not None and not isinstance(options, dict):
            raise HTTPException(status_code=400, detail="options must be an object")

        if _llm_store_enabled(document_store):
            record = document_store.get_llm_conversation(
                {
                    "conversation_id": conversation_id,
                    "account_id": current_user.account_id,
                    "include_messages": True,
                }
            )
            if record is None:
                raise HTTPException(status_code=404, detail="LLM conversation does not exist")
            conversation_snapshot = _llm_conversation_from_record(record)
        else:
            with llm_lock:
                conversation = llm_conversations.get(conversation_id)
                if conversation is None:
                    raise HTTPException(status_code=404, detail="LLM conversation does not exist")
                _authorize_account_match(current_user, conversation.account_id)
                conversation_snapshot = copy.deepcopy(conversation)

        conversation_snapshot = _conversation_with_context_update(
            conversation_snapshot,
            payload,
            config=config,
            document_store=document_store,
        )
        llm_messages = _conversation_messages_for_llm(
            conversation_snapshot,
            content,
            config=config,
            document_store=document_store,
        )
        user_message = ChatMessage(role="user", content=content)
        assistant_message = ChatMessage(role="assistant", content="")

        def event_stream():
            assistant_parts: list[str] = []
            try:
                for delta in _create_llm_client(config).stream_chat(
                    llm_messages,
                    options=_llm_chat_options(config, options),
                ):
                    assistant_parts.append(delta)
                    yield _sse_event("delta", {"delta": delta})
            except ValueError as exc:
                yield _sse_event("error", {"detail": str(exc), "status": 503})
                return
            except RuntimeError as exc:
                yield _sse_event("error", {"detail": str(exc), "status": 502})
                return

            assistant_message.content = "".join(assistant_parts)
            if _llm_store_enabled(document_store):
                now = _es_now()
                title_update = (
                    _conversation_title(content)
                    if _is_default_llm_title(conversation_snapshot.title)
                    else conversation_snapshot.title
                )
                conversation_updates = {
                    "title": title_update,
                    "context_mode": conversation_snapshot.context_mode,
                    "context_document_ids": [
                        str(document.get("document_id") or "")
                        for document in conversation_snapshot.context_documents
                        if str(document.get("document_id") or "").strip()
                    ],
                    "context_documents": [dict(document) for document in conversation_snapshot.context_documents],
                }
                document_store.append_llm_messages(
                    {
                        "conversation_id": conversation_snapshot.conversation_id,
                        "account_id": conversation_snapshot.account_id,
                        "knowledge_base_id": conversation_snapshot.knowledge_base_id,
                        "messages": [
                            {
                                "message_id": user_message.message_id,
                                "role": user_message.role,
                                "content": user_message.content,
                                "created_at": now,
                            },
                            {
                                "message_id": assistant_message.message_id,
                                "role": assistant_message.role,
                                "content": assistant_message.content,
                                "created_at": now,
                            },
                        ],
                        "conversation_updates": conversation_updates,
                    }
                )
                updated = document_store.get_llm_conversation(
                    {
                        "conversation_id": conversation_snapshot.conversation_id,
                        "account_id": conversation_snapshot.account_id,
                        "knowledge_base_id": conversation_snapshot.knowledge_base_id,
                        "include_messages": True,
                    }
                )
                conversation_data = _llm_api_with_appended_messages(
                    updated,
                    conversation_snapshot,
                    user_message,
                    assistant_message,
                )
            else:
                with llm_lock:
                    conversation = llm_conversations.get(conversation_id)
                    if conversation is None:
                        yield _sse_event("error", {"detail": "LLM conversation does not exist", "status": 404})
                        return
                    _authorize_account_match(current_user, conversation.account_id)
                    conversation.context_documents = [dict(document) for document in conversation_snapshot.context_documents]
                    conversation.context_mode = conversation_snapshot.context_mode
                    conversation.messages.extend([user_message, assistant_message])
                    if _is_default_llm_title(conversation.title):
                        conversation.title = _conversation_title(content)
                    conversation.updated_at = _now()
                    conversation_data = conversation.to_dict()

            yield _sse_event(
                "done",
                {
                    "conversation": conversation_data,
                    "user_message": user_message.to_dict(),
                    "assistant_message": assistant_message.to_dict(),
                },
            )

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/jobs")
    async def create_job(
        file: UploadFile = File(...),
        engine: str = Form(DEFAULT_ENGINE),
        account_id: str | None = Form(default=None),
        knowledge_base_id: str = Form(DEFAULT_KNOWLEDGE_BASE_ID),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        current_user = _current_user(auth_config, authorization)
        selected_engine = _normalize_engine(engine)
        if not _is_pdf_upload(file):
            raise HTTPException(status_code=400, detail="只支持 PDF 文件")
        job = await _create_web_job_from_upload(
            file,
            selected_engine,
            web_root,
            upload_root,
            account_id=_authorized_account_id(current_user, account_id),
            knowledge_base_id=_scope_id(knowledge_base_id, DEFAULT_KNOWLEDGE_BASE_ID),
        )
        with jobs_lock:
            jobs[job.job_id] = job
        _save_job_snapshot(document_store, job)

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
        account_id: str | None = Form(default=None),
        knowledge_base_id: str = Form(DEFAULT_KNOWLEDGE_BASE_ID),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        current_user = _current_user(auth_config, authorization)
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
                    account_id=_authorized_account_id(current_user, account_id),
                    knowledge_base_id=_scope_id(knowledge_base_id, DEFAULT_KNOWLEDGE_BASE_ID),
                    batch_id=batch_id,
                    queue_index=index,
                    queue_total=queue_total,
                )
            )

        with jobs_lock:
            for job in created_jobs:
                jobs[job.job_id] = job
        for job in created_jobs:
            _save_job_snapshot(document_store, job)

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

    @app.get("/api/jobs")
    def list_jobs(
        account_id: str | None = None,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
        state: str = "",
        limit: int = 100,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        current_user = _current_user(auth_config, authorization)
        scoped_account_id = _authorized_account_id(current_user, account_id)
        scoped_knowledge_base_id = _scope_id(knowledge_base_id, DEFAULT_KNOWLEDGE_BASE_ID)
        states = _parse_job_states(state)
        merged: dict[str, dict[str, Any]] = {}
        with jobs_lock:
            for job in jobs.values():
                if job.account_id != scoped_account_id or job.knowledge_base_id != scoped_knowledge_base_id:
                    continue
                if states and job.state not in states:
                    continue
                data = job.to_dict()
                data["log_tail"] = _read_log_tail(job.log_path)
                merged[job.job_id] = data
        if document_store is not None:
            for record in document_store.list_jobs(
                account_id=scoped_account_id,
                knowledge_base_id=scoped_knowledge_base_id,
                states=states or None,
                limit=limit,
            ):
                job_id = str(record.get("job_id") or "")
                if job_id and job_id not in merged:
                    record_state = str(record.get("state") or "").strip().lower()
                    if record_state in {"queued", "running"}:
                        continue
                    merged[job_id] = _job_record_to_api(record, scoped_account_id, scoped_knowledge_base_id)
        items = sorted(
            merged.values(),
            key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
            reverse=True,
        )[: max(1, min(int(limit), 500))]
        return {
            "account_id": scoped_account_id,
            "knowledge_base_id": scoped_knowledge_base_id,
            "count": len(items),
            "jobs": items,
        }

    @app.get("/api/jobs/{job_id}")
    def get_job(
        job_id: str,
        account_id: str | None = None,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        current_user = _current_user(auth_config, authorization)
        if _is_document_job_id(job_id):
            return _get_document_job_or_404(
                job_id,
                current_user,
                document_store,
                account_id=account_id,
                knowledge_base_id=knowledge_base_id,
            )
        job = _get_job(job_id, jobs, jobs_lock)
        if job is None:
            return _get_persisted_job_or_404(
                job_id,
                current_user,
                document_store,
                account_id=account_id,
                knowledge_base_id=knowledge_base_id,
            )
        _authorize_account_match(current_user, job.account_id)
        data = job.to_dict()
        data["log_tail"] = _read_log_tail(job.log_path)
        return data

    @app.get("/api/jobs/{job_id}/markdown", response_class=PlainTextResponse)
    def get_markdown(job_id: str, authorization: str | None = Header(default=None)) -> str:
        current_user = _current_user(auth_config, authorization)
        job = _get_job_or_404(job_id, jobs, jobs_lock)
        _authorize_account_match(current_user, job.account_id)
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
    def get_pages(job_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        current_user = _current_user(auth_config, authorization)
        job = _get_job_or_404(job_id, jobs, jobs_lock)
        _authorize_account_match(current_user, job.account_id)
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
    def download_markdown(job_id: str, authorization: str | None = Header(default=None)):
        current_user = _current_user(auth_config, authorization)
        job = _get_job_or_404(job_id, jobs, jobs_lock)
        _authorize_account_match(current_user, job.account_id)
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
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        current_user = _current_user(auth_config, authorization)
        job = _get_job_or_404(job_id, jobs, jobs_lock)
        _authorize_account_match(current_user, job.account_id)
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

    parser = argparse.ArgumentParser(prog="ocean-web", description="Start OCR Assistant web UI.")
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
            failed_job = _get_job(job_id, jobs, jobs_lock)
            _mark_processing_document_failed(document_store, failed_job, config, str(exc))
            _save_job_snapshot(document_store, failed_job, finished=True)
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
                failed_job = _get_job(job_id, jobs, jobs_lock)
                _mark_processing_document_failed(document_store, failed_job, config, str(exc))
                _save_job_snapshot(document_store, failed_job, finished=True)
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
    total_pages = count_pdf_pages(job.input_path)
    _update_job(jobs, jobs_lock, job_id, total_pages=total_pages)
    log(f"Web OCR started: {job.file_name}; engine={job.engine}; pages={total_pages}; max_pages_per_file={max_pages}.")

    primary_client = create_ocr_client(ocr_config)
    fallback_config = _fallback_ocr_config(
        {
            "ocr": ocr_config,
            "ocr_engines": copy.deepcopy(config.get("ocr_engines", {})),
        }
    )
    fallback_client = create_ocr_client(fallback_config) if fallback_config else None
    file_report: dict[str, Any] = {"attempts": []}
    _update_job(jobs, jobs_lock, job_id, progress=12, message=f"提交到 {engine_label} 并等待解析")
    document, used_engine, fallback_used = _recognize_with_fallback(
        pdf=job.input_path,
        primary_client=primary_client,
        primary_config=ocr_config,
        primary_engine=str(ocr_config.get("engine") or job.engine),
        fallback_client=fallback_client,
        fallback_config=fallback_config,
        file_report=file_report,
    )
    used_engine_label = ENGINE_LABELS.get(used_engine, used_engine)
    _update_job(jobs, jobs_lock, job_id, progress=88, message=f"{used_engine_label} 解析完成，正在生成 Markdown")
    if fallback_used:
        log(f"Web OCR fallback used: job={job_id}; engine={used_engine}.")

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
            metadata={
                "web_job_id": job.job_id,
                "requested_ocr_engine": job.engine,
                "ocr_attempts": file_report["attempts"],
                "fallback_used": fallback_used,
            },
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


def _mark_processing_document_failed(
    document_store: ElasticsearchDocumentStore | None,
    job: WebJob | None,
    config: dict[str, Any],
    error: str,
) -> None:
    if document_store is None or job is None or not job.document_id:
        return
    try:
        ocr_config = _web_ocr_config(config, job.engine)
        options = ocr_config.get("options", {})
        file_sha256 = job.file_sha256 or compute_file_sha256(job.input_path)
        processing_fingerprint = job.processing_fingerprint or build_processing_fingerprint(
            file_sha256,
            job.engine,
            options,
        )
        now = _es_now()
        document_store.save_processing_document(
            {
                "account_id": job.account_id,
                "knowledge_base_id": job.knowledge_base_id,
                "document_id": job.document_id,
                "file_name": job.file_name,
                "file_ext": Path(job.file_name).suffix.lower().lstrip("."),
                "mime_type": "application/pdf",
                "file_size": job.input_path.stat().st_size,
                "file_sha256": file_sha256,
                "status": "failed",
                "source": "web_upload",
                "source_path": str(job.input_path),
                "ocr_engine": job.engine,
                "ocr_options_hash": options_hash(options),
                "pipeline_version": PIPELINE_VERSION,
                "processing_fingerprint": processing_fingerprint,
                "page_count": job.total_pages,
                "language": "",
                "title": "",
                "tags": [],
                "metadata": {"web_job_id": job.job_id, "error": error},
                "markdown": "",
                "ocr_json": {},
                "error": error,
                "created_at": job.created_at or now,
                "updated_at": now,
                "processed_at": now,
            }
        )
    except Exception as exc:  # pragma: no cover - depends on external Elasticsearch
        log(f"Failed to mark OCR document as failed in Elasticsearch: {exc}")


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
                "batch_id": job.batch_id,
                "file_name": job.file_name,
                "type": "ocr",
                "state": job.state,
                "progress": job.progress,
                "message": job.message,
                "error": job.error,
                "engine": job.engine,
                "reused": job.reused,
                "queue_index": job.queue_index,
                "queue_total": job.queue_total,
                "total_pages": job.total_pages,
                "created_at": now,
                "updated_at": now,
                "finished_at": now if finished else None,
            }
        )
    except Exception as exc:  # pragma: no cover - depends on external Elasticsearch
        log(f"Failed to save web job snapshot to Elasticsearch: {exc}")


def _auth_config(config: dict[str, Any]) -> dict[str, Any]:
    value = config.get("auth", {})
    return value if isinstance(value, dict) else {}


def _auth_enabled(auth_config: dict[str, Any]) -> bool:
    return _as_bool(auth_config.get("enabled", False))


def _current_user(auth_config: dict[str, Any], authorization: Any = None) -> AuthenticatedUser:
    token = _bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    payload = _decode_token(token, auth_config)
    username = str(payload.get("username") or "").strip()
    account_id = _scope_id(payload.get("account_id"), "")
    if not username or not account_id:
        raise HTTPException(status_code=401, detail="Invalid authentication token")

    if not _auth_enabled(auth_config):
        if username != DEFAULT_ACCOUNT_ID or account_id != DEFAULT_ACCOUNT_ID:
            raise HTTPException(status_code=401, detail="Invalid authentication token")
        return _local_mode_user()

    user_config = _find_auth_user(auth_config, username)
    if not user_config:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    configured_user = _user_from_config(user_config)
    if configured_user.account_id != account_id:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    return configured_user


def _local_mode_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        username=DEFAULT_ACCOUNT_ID,
        account_id=DEFAULT_ACCOUNT_ID,
        role="admin",
        display_name="Local",
        authenticated=True,
    )


def _authorized_account_id(current_user: AuthenticatedUser, requested_account_id: Any = None) -> str:
    requested = str(requested_account_id or "").strip()
    if not current_user.authenticated:
        return requested or DEFAULT_ACCOUNT_ID
    if requested and requested != current_user.account_id:
        raise HTTPException(status_code=403, detail="Account access denied")
    return current_user.account_id


def _authorize_account_match(current_user: AuthenticatedUser, account_id: Any) -> None:
    if not current_user.authenticated:
        return
    if _scope_id(account_id, "") != current_user.account_id:
        raise HTTPException(status_code=403, detail="Account access denied")


def _find_auth_user(auth_config: dict[str, Any], username: str) -> dict[str, Any] | None:
    normalized = str(username or "").strip()
    if not normalized:
        return None
    users = auth_config.get("users") or []
    if not isinstance(users, list):
        return None
    for user in users:
        if isinstance(user, dict) and str(user.get("username") or "").strip() == normalized:
            return user
    return None


def _user_from_config(user_config: dict[str, Any]) -> AuthenticatedUser:
    return AuthenticatedUser(
        username=str(user_config.get("username") or "").strip(),
        account_id=_scope_id(user_config.get("account_id"), DEFAULT_ACCOUNT_ID),
        role=str(user_config.get("role") or "user").strip() or "user",
        display_name=str(user_config.get("display_name") or user_config.get("username") or "").strip(),
        authenticated=True,
    )


def _verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    if password_hash.startswith("sha256:"):
        expected = password_hash.split(":", 1)[1]
        actual = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return hmac.compare_digest(actual, expected)
    if password_hash.startswith("pbkdf2_sha256$"):
        return _verify_pbkdf2_sha256(password, password_hash)
    if password_hash.startswith("$2"):
        try:
            import bcrypt
        except ImportError:
            return False
        return bool(bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8")))
    return False


def _verify_pbkdf2_sha256(password: str, password_hash: str) -> bool:
    try:
        _, iterations, salt, expected = password_hash.split("$", 3)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            int(iterations),
        )
        actual = base64.b64encode(digest).decode("ascii").strip()
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def _encode_token(user: AuthenticatedUser, auth_config: dict[str, Any]) -> str:
    now = int(time.time())
    expires_in = max(1, int(auth_config.get("access_token_minutes", 720))) * 60
    payload = {
        "username": user.username,
        "account_id": user.account_id,
        "role": user.role,
        "iat": now,
        "exp": now + expires_in,
    }
    return _jwt_encode(payload, _jwt_secret(auth_config))


def _decode_token(token: str, auth_config: dict[str, Any]) -> dict[str, Any]:
    payload = _jwt_decode(token, _jwt_secret(auth_config))
    exp = int(payload.get("exp") or 0)
    if exp < int(time.time()):
        raise HTTPException(status_code=401, detail="Authentication token expired")
    return payload


def _jwt_secret(auth_config: dict[str, Any]) -> str:
    secret = str(auth_config.get("jwt_secret") or "").strip()
    if not secret and not _auth_enabled(auth_config):
        return "ocean-local-mode-secret"
    if not secret:
        raise HTTPException(status_code=500, detail="auth.jwt_secret is required when authentication is enabled")
    return secret


def _jwt_encode(payload: dict[str, Any], secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    encoded_header = _base64url_json(header)
    encoded_payload = _base64url_json(payload)
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{encoded_header}.{encoded_payload}.{_base64url_encode(signature)}"


def _jwt_decode(token: str, secret: str) -> dict[str, Any]:
    try:
        encoded_header, encoded_payload, encoded_signature = token.split(".", 2)
        signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
        expected = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
        actual = _base64url_decode(encoded_signature)
        if not hmac.compare_digest(actual, expected):
            raise ValueError("bad signature")
        header = json.loads(_base64url_decode(encoded_header))
        if header.get("alg") != "HS256":
            raise ValueError("unsupported algorithm")
        payload = json.loads(_base64url_decode(encoded_payload))
    except (ValueError, TypeError, json.JSONDecodeError, binascii.Error):
        raise HTTPException(status_code=401, detail="Invalid authentication token") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    return payload


def _base64url_json(value: dict[str, Any]) -> str:
    return _base64url_encode(json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8"))


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _bearer_token(authorization: Any) -> str:
    if not isinstance(authorization, str):
        return ""
    scheme, _, token = authorization.strip().partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return ""
    return token.strip()


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


def _parse_job_states(value: str | None) -> list[str]:
    return [
        item.strip().lower()
        for item in re.split(r"[,，\s]+", str(value or ""))
        if item.strip()
    ]


def _job_record_to_api(
    record: dict[str, Any],
    account_id: str,
    knowledge_base_id: str,
) -> dict[str, Any]:
    job_id = str(record.get("job_id") or "")
    document_id = str(record.get("document_id") or "") or None
    state = str(record.get("state") or "queued").strip().lower() or "queued"
    progress = int(record.get("progress") or (100 if state in {"done", "failed"} else 0))
    done_with_document = state == "done" and bool(document_id)
    query = f"account_id={quote(account_id)}&knowledge_base_id={quote(knowledge_base_id)}"
    return {
        "job_id": job_id,
        "account_id": account_id,
        "knowledge_base_id": knowledge_base_id,
        "document_id": document_id,
        "file_sha256": record.get("file_sha256"),
        "processing_fingerprint": record.get("processing_fingerprint"),
        "reused": bool(record.get("reused")),
        "batch_id": record.get("batch_id"),
        "file_name": record.get("file_name") or job_id,
        "engine": record.get("engine") or "",
        "engine_label": ENGINE_LABELS.get(str(record.get("engine") or ""), str(record.get("engine") or "")),
        "queue_index": record.get("queue_index"),
        "queue_total": record.get("queue_total"),
        "state": state,
        "progress": progress,
        "message": record.get("message") or "",
        "total_pages": record.get("total_pages") or record.get("page_count"),
        "error": record.get("error"),
        "markdown_url": f"/api/documents/{quote(document_id)}/markdown?{query}" if done_with_document else None,
        "download_url": f"/api/documents/{quote(document_id)}/download?{query}" if done_with_document else None,
        "pages_url": f"/api/documents/{quote(document_id)}/pages?{query}" if done_with_document else None,
        "created_at": record.get("created_at") or "",
        "updated_at": record.get("updated_at") or "",
        "log_tail": [],
    }


def _get_persisted_job_or_404(
    job_id: str,
    current_user: AuthenticatedUser,
    document_store: ElasticsearchDocumentStore | None,
    *,
    account_id: str | None = None,
    knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
) -> dict[str, Any]:
    if document_store is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    scoped_account_id = _authorized_account_id(current_user, account_id)
    scoped_knowledge_base_id = _scope_id(knowledge_base_id, DEFAULT_KNOWLEDGE_BASE_ID)
    record = document_store.get_job(
        account_id=scoped_account_id,
        knowledge_base_id=scoped_knowledge_base_id,
        job_id=job_id,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    state = str(record.get("state") or "").strip().lower()
    if state in {"queued", "running"}:
        record = {
            **record,
            "state": "failed",
            "progress": 100,
            "message": "任务不存在或已取消",
            "error": "任务不存在或已取消",
            "updated_at": _es_now(),
            "finished_at": _es_now(),
        }
        try:
            document_store.save_job(record)
        except Exception as exc:  # pragma: no cover - depends on external Elasticsearch
            log(f"Failed to mark orphaned web job as failed: {exc}")
    return _job_record_to_api(record, scoped_account_id, scoped_knowledge_base_id)


def _is_document_job_id(job_id: str) -> bool:
    return str(job_id or "").startswith("document:")


def _document_id_from_job_id(job_id: str) -> str:
    return str(job_id or "").split(":", 1)[1].strip() if _is_document_job_id(job_id) else ""


def _get_document_job_or_404(
    job_id: str,
    current_user: AuthenticatedUser,
    document_store: ElasticsearchDocumentStore | None,
    *,
    account_id: str | None = None,
    knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
) -> dict[str, Any]:
    store = _require_document_store(document_store)
    scoped_account_id = _authorized_account_id(current_user, account_id)
    scoped_knowledge_base_id = _scope_id(knowledge_base_id, DEFAULT_KNOWLEDGE_BASE_ID)
    document_id = _document_id_from_job_id(job_id)
    if not document_id:
        raise HTTPException(status_code=404, detail="Document does not exist")
    record = store.get_document(
        account_id=scoped_account_id,
        knowledge_base_id=scoped_knowledge_base_id,
        document_id=document_id,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Document does not exist")
    return _document_record_to_job_api(record, scoped_account_id, scoped_knowledge_base_id)


def _document_record_to_job_api(
    record: dict[str, Any],
    account_id: str,
    knowledge_base_id: str,
) -> dict[str, Any]:
    document_id = str(record.get("document_id") or "")
    status = str(record.get("status") or "").strip().lower()
    state = "done" if status == "done" else "failed" if status == "failed" else "running"
    progress = 100 if state in {"done", "failed"} else 50
    query = f"account_id={quote(account_id)}&knowledge_base_id={quote(knowledge_base_id)}"
    done = state == "done"
    return {
        "job_id": f"document:{document_id}",
        "account_id": account_id,
        "knowledge_base_id": knowledge_base_id,
        "document_id": document_id,
        "file_sha256": record.get("file_sha256"),
        "processing_fingerprint": record.get("processing_fingerprint"),
        "reused": True,
        "batch_id": None,
        "file_name": record.get("file_name") or document_id,
        "engine": record.get("ocr_engine") or "library",
        "engine_label": record.get("ocr_engine") or "Knowledge Base",
        "queue_index": None,
        "queue_total": None,
        "state": state,
        "progress": progress,
        "message": "Loaded from knowledge base" if done else f"Document status: {status or state}",
        "total_pages": record.get("page_count"),
        "error": record.get("error") if state == "failed" else None,
        "markdown_url": f"/api/documents/{quote(document_id)}/markdown?{query}" if done else None,
        "download_url": f"/api/documents/{quote(document_id)}/download?{query}" if done else None,
        "pages_url": f"/api/documents/{quote(document_id)}/pages?{query}" if done else None,
        "created_at": record.get("created_at") or "",
        "updated_at": record.get("updated_at") or record.get("processed_at") or "",
        "log_tail": [],
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
        raise HTTPException(status_code=400, detail="请至少输入一个关键词")
    normalized_mode = str(match_mode or "any").lower()
    if normalized_mode not in {"any", "all"}:
        raise HTTPException(status_code=400, detail="match_mode 只支持 any 或 all")
    normalized_granularity = str(granularity or "paragraph").lower()
    if normalized_granularity not in {"paragraph", "page"}:
        raise HTTPException(status_code=400, detail="granularity 只支持 paragraph 或 page")

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


def _llm_conversation_config(config: dict[str, Any]) -> dict[str, int]:
    llm_config = _llm_config(config)
    conversation_config = llm_config.get("conversation")
    if not isinstance(conversation_config, dict):
        conversation_config = {}
    max_context_documents = _int_config(
        conversation_config.get("max_context_documents", llm_config.get("max_context_documents")),
        DEFAULT_MAX_CONTEXT_DOCUMENTS,
    )
    return {
        "max_context_documents": max(1, min(max_context_documents, DEFAULT_MAX_CONTEXT_DOCUMENTS)),
        "max_document_context_chars": max(
            1,
            _int_config(
                conversation_config.get(
                    "max_document_context_chars",
                    llm_config.get("max_document_context_chars"),
                ),
                DEFAULT_MAX_DOCUMENT_CONTEXT_CHARS,
            ),
        ),
        "max_history_messages": max(
            0,
            _int_config(
                conversation_config.get("max_history_messages", llm_config.get("max_history_messages")),
                DEFAULT_MAX_HISTORY_MESSAGES,
            ),
        ),
    }


def _int_config(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _llm_config_ready(config: dict[str, Any]) -> bool:
    return bool(config.get("api_base_url") and config.get("api_key") and config.get("model"))


def _create_llm_client(config: dict[str, Any]) -> OpenAICompatibleClient:
    llm_config = _llm_config(config)
    provider = str(llm_config.get("provider") or "openai_compatible").strip().lower()
    if provider not in {"openai_compatible", "openai-compatible", "openai"}:
        raise HTTPException(status_code=400, detail=f"Unsupported LLM provider: {provider}")
    return OpenAICompatibleClient.from_config(llm_config)


def _llm_chat_options(config: dict[str, Any], options: dict[str, Any] | None = None) -> dict[str, Any] | None:
    merged = dict(options or {})
    web_search_enabled = merged.pop("web_search_enabled", None)
    if "tools" in merged or "tool_choice" in merged:
        return merged or None
    if web_search_enabled is False:
        return merged or None

    web_search = _llm_web_search_options(config)
    if web_search is None:
        return merged or None

    merged["tools"] = [web_search]
    merged.setdefault("tool_choice", "auto")
    return merged


def _llm_web_search_options(config: dict[str, Any]) -> dict[str, Any] | None:
    llm_config = _llm_config(config)
    web_search_config = llm_config.get("web_search")
    if not isinstance(web_search_config, dict) or not web_search_config.get("enabled"):
        return None

    tool: dict[str, Any] = {"type": str(web_search_config.get("type") or "web_search")}
    for key in ("max_keyword", "force_search", "limit"):
        if key in web_search_config:
            tool[key] = web_search_config[key]
    return tool


def _create_llm_conversation(
    payload: dict[str, Any],
    config: dict[str, Any],
    *,
    account_id: str | None = None,
    knowledge_base_id: str | None = None,
    document_store: ElasticsearchDocumentStore | None = None,
) -> LlmConversation:
    llm_config = _llm_config(config)
    conversation_config = _llm_conversation_config(config)
    title = _payload_text(payload, "title") or _default_llm_title()
    origin = _normalize_llm_origin(payload.get("origin"))
    system_prompt = _payload_text(payload, "system_prompt") or str(llm_config.get("system_prompt") or "").strip()
    scoped_account_id = _scope_id(account_id if account_id is not None else payload.get("account_id"), DEFAULT_ACCOUNT_ID)
    scoped_knowledge_base_id = _scope_id(
        knowledge_base_id if knowledge_base_id is not None else payload.get("knowledge_base_id"),
        DEFAULT_KNOWLEDGE_BASE_ID,
    )
    context_document_ids = _parse_llm_context_document_ids(
        payload,
        max_documents=conversation_config["max_context_documents"],
    )
    context_documents = _load_llm_context_document_snapshots(
        document_store,
        account_id=scoped_account_id,
        knowledge_base_id=scoped_knowledge_base_id,
        document_ids=context_document_ids,
    )
    conversation = LlmConversation(
        conversation_id=uuid.uuid4().hex[:12],
        title=title,
        account_id=scoped_account_id,
        knowledge_base_id=scoped_knowledge_base_id,
        origin=origin,
        system_prompt=system_prompt,
        context_mode=LLM_CONTEXT_MODE_DOCUMENTS if context_documents else LLM_CONTEXT_MODE_NONE,
        context_documents=context_documents,
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


def _llm_store_enabled(document_store: Any) -> bool:
    return all(
        callable(getattr(document_store, method_name, None))
        for method_name in (
            "save_llm_conversation",
            "list_llm_conversations",
            "get_llm_conversation",
            "append_llm_messages",
            "soft_delete_llm_conversation",
        )
    )


def _llm_conversation_record(conversation: LlmConversation, config: dict[str, Any]) -> dict[str, Any]:
    llm_config = _llm_config(config)
    now = _es_now()
    record = conversation.to_dict(include_messages=False)
    record.update(
        {
            "provider": llm_config.get("provider") or "openai_compatible",
            "model": llm_config.get("model") or "",
            "temperature": float(llm_config.get("temperature", 0)),
            "max_tokens": int(llm_config.get("max_tokens", 4096)),
            "message_count": len(conversation.messages),
            "status": "active",
            "created_at": now,
            "updated_at": now,
            "deleted_at": None,
        }
    )
    return record


def _llm_record_to_api(record: dict[str, Any], *, include_messages: bool = True) -> dict[str, Any]:
    context_documents = record.get("context_documents")
    if not isinstance(context_documents, list):
        context_documents = []
    context_document_ids = record.get("context_document_ids")
    if not isinstance(context_document_ids, list):
        context_document_ids = [
            str(document.get("document_id") or "")
            for document in context_documents
            if isinstance(document, dict) and str(document.get("document_id") or "").strip()
        ]
    messages = record.get("messages")
    data = {
        "conversation_id": record.get("conversation_id") or "",
        "account_id": record.get("account_id") or DEFAULT_ACCOUNT_ID,
        "knowledge_base_id": record.get("knowledge_base_id") or DEFAULT_KNOWLEDGE_BASE_ID,
        "title": record.get("title") or _default_llm_title(),
        "origin": _normalize_llm_origin(record.get("origin"), record=record),
        "system_prompt": record.get("system_prompt") or "",
        "context_mode": record.get("context_mode") or (LLM_CONTEXT_MODE_DOCUMENTS if context_document_ids else LLM_CONTEXT_MODE_NONE),
        "context_document_ids": [str(document_id) for document_id in context_document_ids if str(document_id).strip()],
        "context_documents": [dict(document) for document in context_documents if isinstance(document, dict)],
        "message_count": int(record.get("message_count") or (len(messages) if isinstance(messages, list) else 0)),
        "created_at": record.get("created_at") or "",
        "updated_at": record.get("updated_at") or "",
    }
    if include_messages:
        data["messages"] = [
            {
                "message_id": message.get("message_id") or "",
                "role": message.get("role") or "",
                "content": message.get("content") or "",
                "created_at": message.get("created_at") or "",
            }
            for message in (messages if isinstance(messages, list) else [])
            if isinstance(message, dict)
        ]
    return data


def _llm_api_with_appended_messages(
    record: dict[str, Any] | None,
    conversation_snapshot: LlmConversation,
    user_message: ChatMessage,
    assistant_message: ChatMessage,
) -> dict[str, Any]:
    data = _llm_record_to_api(record, include_messages=True) if record else conversation_snapshot.to_dict()
    fallback_messages = [
        message.to_dict()
        for message in [
            *conversation_snapshot.messages,
            user_message,
            assistant_message,
        ]
    ]
    if len(data.get("messages") or []) < len(fallback_messages):
        data["messages"] = fallback_messages
        data["message_count"] = len(fallback_messages)
    return data


def _llm_conversation_from_record(record: dict[str, Any]) -> LlmConversation:
    messages = [
        ChatMessage(
            role=str(message.get("role") or ""),
            content=str(message.get("content") or ""),
            created_at=str(message.get("created_at") or _now()),
            message_id=str(message.get("message_id") or uuid.uuid4().hex[:12]),
        )
        for message in record.get("messages", [])
        if isinstance(message, dict)
    ]
    return LlmConversation(
        conversation_id=str(record.get("conversation_id") or ""),
        title=str(record.get("title") or _default_llm_title()),
        account_id=str(record.get("account_id") or DEFAULT_ACCOUNT_ID),
        knowledge_base_id=str(record.get("knowledge_base_id") or DEFAULT_KNOWLEDGE_BASE_ID),
        origin=_normalize_llm_origin(record.get("origin"), record=record),
        system_prompt=str(record.get("system_prompt") or ""),
        context_mode=str(record.get("context_mode") or LLM_CONTEXT_MODE_NONE),
        context_documents=[
            dict(document)
            for document in record.get("context_documents", [])
            if isinstance(document, dict)
        ],
        messages=messages,
        created_at=str(record.get("created_at") or _now()),
        updated_at=str(record.get("updated_at") or _now()),
    )


def _normalize_llm_origin(value: Any, *, record: dict[str, Any] | None = None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"reader", "global"}:
        return normalized
    if record:
        metadata = record.get("metadata")
        if isinstance(metadata, dict):
            metadata_origin = str(metadata.get("origin") or "").strip().lower()
            if metadata_origin in {"reader", "global"}:
                return metadata_origin
        # Legacy conversations did not store origin. Context-bound legacy
        # conversations were reader-created in the original UI flow.
        context_ids = record.get("context_document_ids")
        context_documents = record.get("context_documents")
        if (isinstance(context_ids, list) and context_ids) or (isinstance(context_documents, list) and context_documents):
            return "reader"
    return "global"


def _normalize_llm_context_mode_filter(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return ""
    if normalized not in {LLM_CONTEXT_MODE_NONE, LLM_CONTEXT_MODE_DOCUMENTS}:
        raise HTTPException(status_code=400, detail="context_mode only supports none or documents")
    return normalized


def _llm_conversation_matches(
    conversation: LlmConversation,
    *,
    account_id: str,
    knowledge_base_id: str,
    document_id: str = "",
    context_mode: str = "",
) -> bool:
    if conversation.account_id != account_id or conversation.knowledge_base_id != knowledge_base_id:
        return False
    if context_mode and conversation.context_mode != context_mode:
        return False
    if document_id:
        return document_id in {
            str(document.get("document_id") or "").strip()
            for document in conversation.context_documents
            if isinstance(document, dict)
        }
    return True


def _parse_llm_context_document_ids(payload: dict[str, Any], *, max_documents: int) -> list[str]:
    raw_documents = payload.get("context_documents")
    if raw_documents is None:
        raw_documents = payload.get("context_document_ids")
    if raw_documents is None:
        return []
    if not isinstance(raw_documents, list):
        raise HTTPException(status_code=400, detail="context_documents must be an array")

    document_ids: list[str] = []
    for raw_document in raw_documents:
        if isinstance(raw_document, dict):
            document_id = str(raw_document.get("document_id") or "").strip()
        else:
            document_id = str(raw_document or "").strip()
        if not document_id:
            raise HTTPException(status_code=400, detail="context document_id is required")
        if document_id in document_ids:
            raise HTTPException(status_code=400, detail="context document_id must be unique")
        document_ids.append(document_id)

    if len(document_ids) > max_documents:
        raise HTTPException(status_code=400, detail=f"context_documents supports at most {max_documents} documents")
    return document_ids


def _conversation_with_context_update(
    conversation: LlmConversation,
    payload: dict[str, Any],
    *,
    config: dict[str, Any],
    document_store: ElasticsearchDocumentStore | None = None,
) -> LlmConversation:
    if "context_documents" not in payload and "context_document_ids" not in payload:
        return conversation
    if conversation.origin == "reader":
        raise HTTPException(status_code=400, detail="Reader-origin conversation context cannot be modified")
    conversation_config = _llm_conversation_config(config)
    document_ids = _parse_llm_context_document_ids(payload, max_documents=conversation_config["max_context_documents"])
    context_documents = _load_llm_context_document_snapshots(
        document_store,
        account_id=conversation.account_id,
        knowledge_base_id=conversation.knowledge_base_id,
        document_ids=document_ids,
    )
    conversation.context_documents = context_documents
    conversation.context_mode = LLM_CONTEXT_MODE_DOCUMENTS if context_documents else LLM_CONTEXT_MODE_NONE
    return conversation


def _load_llm_context_document_snapshots(
    document_store: Any,
    *,
    account_id: str,
    knowledge_base_id: str,
    document_ids: list[str],
) -> list[dict[str, Any]]:
    if not document_ids:
        return []
    if document_store is None or not callable(getattr(document_store, "get_document", None)):
        raise HTTPException(status_code=503, detail="Document store is required for LLM document context")

    snapshots: list[dict[str, Any]] = []
    for index, document_id in enumerate(document_ids):
        record = document_store.get_document(
            account_id=account_id,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
        )
        if record is None:
            raise HTTPException(status_code=404, detail=f"Context document does not exist or is not accessible: {document_id}")
        file_name = str(record.get("file_name") or record.get("title") or document_id)
        snapshots.append(
            {
                "document_id": document_id,
                "file_name": file_name,
                "title": str(record.get("title") or file_name),
                "account_id": str(record.get("account_id") or account_id),
                "knowledge_base_id": str(record.get("knowledge_base_id") or knowledge_base_id),
                "page_count": record.get("page_count"),
                "ocr_engine": record.get("ocr_engine") or "",
                "order": index,
            }
        )
    return snapshots


def _conversation_messages_for_llm(
    conversation: LlmConversation,
    user_content: str,
    *,
    config: dict[str, Any] | None = None,
    document_store: Any = None,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    conversation_config = _llm_conversation_config(config or {})
    system_content = conversation.system_prompt.strip()
    context_content = _llm_context_prompt(
        conversation,
        document_store=document_store,
        max_chars=conversation_config["max_document_context_chars"],
    )
    if context_content:
        system_content = "\n\n".join(
            part
            for part in [
                system_content,
                "Use the following knowledge base documents as context. When using document content, cite the document name and page number when possible.",
                context_content,
            ]
            if part
        )
    if system_content:
        messages.append({"role": "system", "content": system_content})

    history = [
        message
        for message in conversation.messages
        if message.role in {"user", "assistant"} and message.content.strip()
    ]
    max_history_messages = conversation_config["max_history_messages"]
    if max_history_messages:
        history = history[-max_history_messages:]
    elif max_history_messages == 0:
        history = []
    for message in history:
        if message.role in {"user", "assistant"} and message.content.strip():
            messages.append({"role": message.role, "content": message.content.strip()})
    messages.append({"role": "user", "content": user_content.strip()})
    return messages


def _llm_context_prompt(
    conversation: LlmConversation,
    *,
    document_store: Any,
    max_chars: int,
) -> str:
    if not conversation.context_documents:
        return ""
    if document_store is None or not callable(getattr(document_store, "load_ocr_document", None)):
        raise HTTPException(status_code=503, detail="Document store is required for LLM document context")

    blocks: list[str] = []
    remaining = max(1, int(max_chars))
    for index, snapshot in enumerate(conversation.context_documents, start=1):
        document_id = str(snapshot.get("document_id") or "").strip()
        if not document_id:
            continue
        document = document_store.load_ocr_document(
            account_id=conversation.account_id,
            knowledge_base_id=conversation.knowledge_base_id,
            document_id=document_id,
        )
        if document is None:
            raise HTTPException(status_code=404, detail=f"Context document OCR result is not ready: {document_id}")
        file_name = str(snapshot.get("file_name") or document.source_file or document_id)
        lines = [
            f"[Document {index}]",
            f"document_id: {document_id}",
            f"file_name: {file_name}",
            "",
        ]
        for page in document.pages:
            page_text = page.text.strip()
            if not page_text:
                continue
            lines.extend([f"Page {page.page_number}:", page_text, ""])
        block = "\n".join(lines).strip()
        if not block:
            continue
        if len(block) > remaining:
            block = block[:remaining].rstrip()
        blocks.append(block)
        remaining -= len(block)
        if remaining <= 0:
            break
    return "\n\n".join(blocks)


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


def _is_default_llm_title(title: Any) -> bool:
    normalized = str(title or "").strip()
    return normalized in {_default_llm_title(), "New chat", "新对话"}


def _default_llm_title() -> str:
    return "New chat"


def _sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


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


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _es_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")



if __name__ == "__main__":
    main()
