from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ocean.extractors import chunk_by_pages
from ocean.models import OcrBlock, OcrDocument, OcrPage

PIPELINE_VERSION = "ocean-ocr-cache-v1"
DEFAULT_ACCOUNT_ID = "local"
DEFAULT_KNOWLEDGE_BASE_ID = "default"
PUBLIC_ACCOUNT_ID = "__public__"


def create_document_store(config: dict[str, Any] | None) -> ElasticsearchDocumentStore | None:
    config = config or {}
    if not _as_bool(config.get("enabled", False)):
        return None
    store = ElasticsearchDocumentStore(config)
    store.ensure_indices()
    return store


def compute_file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        while chunk := f.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def build_processing_fingerprint(file_sha256: str, engine: str, options: dict[str, Any]) -> str:
    payload = {
        "file_sha256": file_sha256,
        "ocr_engine": engine,
        "ocr_options": _json_safe(options),
        "pipeline_version": PIPELINE_VERSION,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ElasticsearchDocumentStore:
    def __init__(self, config: dict[str, Any]) -> None:
        try:
            from elasticsearch import Elasticsearch
        except ImportError as exc:  # pragma: no cover - depends on optional runtime install
            raise RuntimeError("elasticsearch is required when elasticsearch.enabled=true. Run: pip install -e .") from exc

        self.index_prefix = str(config.get("index_prefix") or "ocean").strip() or "ocean"
        hosts = config.get("hosts") or ["http://127.0.0.1:9200"]
        username = str(config.get("username") or "")
        password = str(config.get("password") or "")
        basic_auth = (username, password) if username or password else None
        self.request_timeout = int(config.get("request_timeout_seconds", 30))
        self.analyzer = str(config.get("analyzer") or "ik_max_word")
        self.search_analyzer = str(config.get("search_analyzer") or "ik_smart")
        self.client = Elasticsearch(
            hosts,
            basic_auth=basic_auth,
            verify_certs=_as_bool(config.get("verify_certs", True)),
            request_timeout=self.request_timeout,
        )

    @property
    def documents_index(self) -> str:
        return f"{self.index_prefix}_documents_v1"

    @property
    def pages_index(self) -> str:
        return f"{self.index_prefix}_pages_v1"

    @property
    def chunks_index(self) -> str:
        return f"{self.index_prefix}_chunks_v1"

    @property
    def jobs_index(self) -> str:
        return f"{self.index_prefix}_jobs_v1"

    @property
    def llm_conversations_index(self) -> str:
        return f"{self.index_prefix}_llm_conversations_v1"

    @property
    def llm_messages_index(self) -> str:
        return f"{self.index_prefix}_llm_messages_v1"

    def ensure_indices(self) -> None:
        self._ensure_index(self.documents_index, self._document_mapping())
        self._ensure_index(self.pages_index, self._page_mapping())
        self._ensure_index(self.chunks_index, self._chunk_mapping())
        self._ensure_index(self.jobs_index, self._job_mapping())
        self._ensure_index(self.llm_conversations_index, self._llm_conversation_mapping())
        self._ensure_index(self.llm_messages_index, self._llm_message_mapping())

    def find_processed_document(
        self,
        *,
        account_id: str,
        knowledge_base_id: str,
        file_sha256: str,
        processing_fingerprint: str,
    ) -> dict[str, Any] | None:
        response = self.client.search(
            index=self.documents_index,
            size=1,
            sort=[{"processed_at": {"order": "desc", "missing": "_last"}}],
            query={
                "bool": {
                    "filter": [
                        {"term": {"account_id": account_id}},
                        {"term": {"knowledge_base_id": knowledge_base_id}},
                        {"term": {"file_sha256": file_sha256}},
                        {"term": {"processing_fingerprint": processing_fingerprint}},
                        {"term": {"status": "done"}},
                    ]
                }
            },
        )
        hits = response.get("hits", {}).get("hits", [])
        return hits[0].get("_source") if hits else None

    def get_document(
        self,
        *,
        account_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> dict[str, Any] | None:
        response = self.client.search(
            index=self.documents_index,
            size=1,
            query={
                "bool": {
                    "filter": [
                        {"terms": {"account_id": _visible_account_ids(account_id)}},
                        {"term": {"knowledge_base_id": knowledge_base_id}},
                        {"term": {"document_id": document_id}},
                        {"bool": {"must_not": [{"term": {"status": "deleted"}}]}},
                    ]
                }
            },
        )
        hits = response.get("hits", {}).get("hits", [])
        return hits[0].get("_source") if hits else None

    def list_documents(
        self,
        *,
        account_id: str,
        knowledge_base_id: str,
        query_text: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        filters: list[dict[str, Any]] = [
            {"terms": {"account_id": _visible_account_ids(account_id)}},
            {"term": {"knowledge_base_id": knowledge_base_id}},
            {"term": {"status": "done"}},
        ]
        query: dict[str, Any] = {"bool": {"filter": filters}}
        if query_text.strip():
            query["bool"]["must"] = [
                {
                    "multi_match": {
                        "query": query_text.strip(),
                        "fields": ["file_name^3", "title^2", "markdown"],
                    }
                }
            ]
        response = self.client.search(
            index=self.documents_index,
            body={
                "size": max(1, min(int(limit), 500)),
                "_source": {"excludes": ["markdown", "ocr_json", "metadata"]},
                "sort": [{"processed_at": {"order": "desc", "missing": "_last"}}],
                "query": query,
            },
        )
        return [hit.get("_source", {}) for hit in response.get("hits", {}).get("hits", [])]

    def save_processing_document(self, document: dict[str, Any]) -> None:
        self.client.index(
            index=self.documents_index,
            id=document["document_id"],
            document=document,
        )

    def save_job(self, job: dict[str, Any]) -> None:
        self.client.index(index=self.jobs_index, id=job["job_id"], document=job)

    def get_job(
        self,
        *,
        account_id: str,
        knowledge_base_id: str,
        job_id: str,
    ) -> dict[str, Any] | None:
        response = self.client.search(
            index=self.jobs_index,
            size=1,
            query={
                "bool": {
                    "filter": [
                        {"term": {"account_id": account_id}},
                        {"term": {"knowledge_base_id": knowledge_base_id}},
                        {"term": {"job_id": job_id}},
                    ]
                }
            },
        )
        hits = response.get("hits", {}).get("hits", [])
        return hits[0].get("_source") if hits else None

    def list_jobs(
        self,
        *,
        account_id: str,
        knowledge_base_id: str,
        states: list[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        filters: list[dict[str, Any]] = [
            {"term": {"account_id": account_id}},
            {"term": {"knowledge_base_id": knowledge_base_id}},
        ]
        normalized_states = [state for state in (states or []) if state]
        if normalized_states:
            filters.append({"terms": {"state": normalized_states}})
        response = self.client.search(
            index=self.jobs_index,
            body={
                "size": max(1, min(int(limit), 500)),
                "sort": [{"updated_at": {"order": "desc", "missing": "_last"}}],
                "query": {"bool": {"filter": filters}},
            },
        )
        return [hit.get("_source", {}) for hit in response.get("hits", {}).get("hits", [])]

    def save_llm_conversation(self, conversation: dict[str, Any]) -> dict[str, Any]:
        now = _now()
        record = dict(conversation or {})
        conversation_id = _non_empty_str(record.get("conversation_id")) or str(uuid4())
        context_document_ids = _llm_context_document_ids(record)
        context_documents = record.get("context_documents")
        record.update(
            {
                "conversation_id": conversation_id,
                "account_id": _non_empty_str(record.get("account_id")) or DEFAULT_ACCOUNT_ID,
                "knowledge_base_id": _non_empty_str(record.get("knowledge_base_id"))
                or DEFAULT_KNOWLEDGE_BASE_ID,
                "context_mode": _non_empty_str(record.get("context_mode"))
                or ("documents" if context_document_ids else "none"),
                "context_document_ids": context_document_ids,
                "context_documents": context_documents if isinstance(context_documents, list) else [],
                "message_count": _int_or_default(record.get("message_count"), 0),
                "status": _non_empty_str(record.get("status")) or "active",
                "created_at": _non_empty_str(record.get("created_at")) or now,
                "updated_at": _non_empty_str(record.get("updated_at")) or now,
            }
        )
        record.setdefault("title", "")
        record.setdefault("deleted_at", None)
        self.client.index(
            index=self.llm_conversations_index,
            id=conversation_id,
            document=record,
        )
        return record

    def list_llm_conversations(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        filters = filters or {}
        query_filters: list[dict[str, Any]] = [
            {"term": {"account_id": _non_empty_str(filters.get("account_id")) or DEFAULT_ACCOUNT_ID}},
            {
                "term": {
                    "knowledge_base_id": _non_empty_str(filters.get("knowledge_base_id"))
                    or DEFAULT_KNOWLEDGE_BASE_ID
                }
            },
        ]
        document_id = _non_empty_str(filters.get("document_id"))
        if document_id:
            query_filters.append({"term": {"context_document_ids": document_id}})
        context_mode = _non_empty_str(filters.get("context_mode"))
        if context_mode:
            query_filters.append({"term": {"context_mode": context_mode}})

        bool_query: dict[str, Any] = {"filter": query_filters}
        status = _non_empty_str(filters.get("status"))
        if status:
            query_filters.append({"term": {"status": status}})
        elif not _as_bool(filters.get("include_deleted", False)):
            bool_query["must_not"] = [{"term": {"status": "deleted"}}]

        response = self.client.search(
            index=self.llm_conversations_index,
            body={
                "size": _bounded_int(filters.get("limit"), default=100, minimum=1, maximum=500),
                "sort": [{"updated_at": {"order": "desc", "missing": "_last"}}],
                "query": {"bool": bool_query},
            },
        )
        return [hit.get("_source", {}) for hit in response.get("hits", {}).get("hits", [])]

    def get_llm_conversation(self, query: dict[str, Any]) -> dict[str, Any] | None:
        query = query or {}
        conversation_id = _non_empty_str(query.get("conversation_id"))
        if not conversation_id:
            return None
        source = self._get_source_by_id(self.llm_conversations_index, conversation_id)
        if source is None:
            return None
        if not _llm_source_matches_scope(source, query):
            return None
        if source.get("status") == "deleted" and not _as_bool(query.get("include_deleted", False)):
            return None

        result = dict(source)
        if _as_bool(query.get("include_messages", False)):
            result["messages"] = self.list_llm_messages(
                {
                    "conversation_id": conversation_id,
                    "account_id": source.get("account_id"),
                    "knowledge_base_id": source.get("knowledge_base_id"),
                    "limit": query.get("messages_limit", query.get("limit", 1000)),
                }
            )
        return result

    def append_llm_messages(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        payload = payload or {}
        conversation_id = _non_empty_str(payload.get("conversation_id"))
        if not conversation_id:
            raise ValueError("conversation_id is required")

        conversation = self.get_llm_conversation(
            {
                "conversation_id": conversation_id,
                "account_id": payload.get("account_id"),
                "knowledge_base_id": payload.get("knowledge_base_id"),
            }
        )
        if conversation is None:
            raise ValueError("LLM conversation does not exist")

        raw_messages = payload.get("messages") or []
        if isinstance(raw_messages, dict):
            raw_messages = [raw_messages]
        if not isinstance(raw_messages, list):
            raise ValueError("messages must be a list")

        now = _non_empty_str(payload.get("created_at")) or _non_empty_str(payload.get("updated_at")) or _now()
        account_id = _non_empty_str(payload.get("account_id")) or _non_empty_str(conversation.get("account_id"))
        knowledge_base_id = _non_empty_str(payload.get("knowledge_base_id")) or _non_empty_str(
            conversation.get("knowledge_base_id")
        )
        next_sequence = _append_start_sequence(payload, conversation)
        records: list[dict[str, Any]] = []
        for message in raw_messages:
            if not isinstance(message, dict):
                raise ValueError("messages must contain objects")
            record = dict(message)
            sequence = record.get("sequence")
            if sequence is None or sequence == "":
                next_sequence += 1
                sequence = next_sequence
            else:
                sequence = int(sequence)
                next_sequence = max(next_sequence, sequence)
            message_id = _non_empty_str(record.get("message_id")) or str(uuid4())
            record.update(
                {
                    "message_id": message_id,
                    "conversation_id": conversation_id,
                    "account_id": account_id or DEFAULT_ACCOUNT_ID,
                    "knowledge_base_id": knowledge_base_id or DEFAULT_KNOWLEDGE_BASE_ID,
                    "sequence": sequence,
                    "created_at": _non_empty_str(record.get("created_at")) or now,
                }
            )
            record.setdefault("metadata", {})
            records.append(record)

        for record in records:
            self.client.index(
                index=self.llm_messages_index,
                id=record["message_id"],
                document=record,
            )

        if records:
            current_count = _int_or_default(conversation.get("message_count"), 0)
            conversation_updates = payload.get("conversation_updates")
            update_doc = dict(conversation_updates) if isinstance(conversation_updates, dict) else {}
            update_doc.update(
                {
                    "message_count": _int_or_default(
                        payload.get("message_count"),
                        current_count + len(records),
                    ),
                    "updated_at": _non_empty_str(payload.get("updated_at")) or now,
                }
            )
            self.client.update(
                index=self.llm_conversations_index,
                id=conversation_id,
                doc=update_doc,
            )

        return sorted(records, key=lambda record: int(record.get("sequence") or 0))

    def list_llm_messages(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        query = query or {}
        conversation_id = _non_empty_str(query.get("conversation_id"))
        if not conversation_id:
            return []
        query_filters: list[dict[str, Any]] = [{"term": {"conversation_id": conversation_id}}]
        account_id = _non_empty_str(query.get("account_id"))
        if account_id:
            query_filters.append({"term": {"account_id": account_id}})
        knowledge_base_id = _non_empty_str(query.get("knowledge_base_id"))
        if knowledge_base_id:
            query_filters.append({"term": {"knowledge_base_id": knowledge_base_id}})

        response = self.client.search(
            index=self.llm_messages_index,
            body={
                "size": _bounded_int(query.get("limit"), default=1000, minimum=1, maximum=5000),
                "sort": [
                    {"sequence": {"order": "asc", "missing": "_last"}},
                    {"created_at": {"order": "asc", "missing": "_last"}},
                ],
                "query": {"bool": {"filter": query_filters}},
            },
        )
        return [hit.get("_source", {}) for hit in response.get("hits", {}).get("hits", [])]

    def soft_delete_llm_conversation(self, query: dict[str, Any]) -> dict[str, Any] | None:
        query = query or {}
        conversation = self.get_llm_conversation(query)
        if conversation is None:
            return None
        now = _non_empty_str(query.get("deleted_at")) or _now()
        updates = {
            "status": "deleted",
            "deleted_at": now,
            "updated_at": _non_empty_str(query.get("updated_at")) or now,
        }
        self.client.update(
            index=self.llm_conversations_index,
            id=conversation["conversation_id"],
            doc=updates,
        )
        result = dict(conversation)
        result.update(updates)
        return result

    def save_ocr_result(
        self,
        *,
        account_id: str,
        knowledge_base_id: str,
        document_id: str,
        file_name: str,
        source_path: str,
        file_size: int,
        file_sha256: str,
        processing_fingerprint: str,
        ocr_options_hash: str,
        document: OcrDocument,
        markdown: str,
        chunk_pages: int = 3,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = _now()
        record = {
            "account_id": account_id,
            "knowledge_base_id": knowledge_base_id,
            "document_id": document_id,
            "file_name": file_name,
            "file_ext": Path(file_name).suffix.lower().lstrip("."),
            "mime_type": "application/pdf",
            "file_size": file_size,
            "file_sha256": file_sha256,
            "status": "done",
            "source": "web_upload",
            "source_path": source_path,
            "ocr_engine": document.ocr_engine,
            "ocr_options_hash": ocr_options_hash,
            "pipeline_version": PIPELINE_VERSION,
            "processing_fingerprint": processing_fingerprint,
            "page_count": len(document.pages),
            "language": (metadata or {}).get("language", ""),
            "title": (metadata or {}).get("title", ""),
            "tags": (metadata or {}).get("tags", []),
            "metadata": metadata or {},
            "markdown": markdown,
            "ocr_json": document.to_dict(),
            "created_at": now,
            "updated_at": now,
            "processed_at": now,
        }
        self.client.index(index=self.documents_index, id=document_id, document=record)
        self._bulk_index(self._page_actions(account_id, knowledge_base_id, document_id, document, now))
        self._bulk_index(self._chunk_actions(account_id, knowledge_base_id, document_id, document, chunk_pages, now))

    def get_markdown(
        self,
        *,
        account_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> str | None:
        document = self.get_document(
            account_id=account_id,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
        )
        if not document:
            return None
        markdown = document.get("markdown")
        return markdown if isinstance(markdown, str) else None

    def get_pages(
        self,
        *,
        account_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> list[dict[str, Any]]:
        response = self.client.search(
            index=self.pages_index,
            size=10000,
            sort=[{"page_number": {"order": "asc"}}],
            query={
                "bool": {
                    "filter": [
                        {"terms": {"account_id": _visible_account_ids(account_id)}},
                        {"term": {"knowledge_base_id": knowledge_base_id}},
                        {"term": {"document_id": document_id}},
                    ]
                }
            },
        )
        return [hit.get("_source", {}) for hit in response.get("hits", {}).get("hits", [])]

    def load_ocr_document(
        self,
        *,
        account_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> OcrDocument | None:
        document_record = self.get_document(
            account_id=account_id,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
        )
        if not document_record:
            return None
        ocr_json = document_record.get("ocr_json")
        if isinstance(ocr_json, dict) and ocr_json.get("pages"):
            from ocean.models import ocr_document_from_dict

            return ocr_document_from_dict(ocr_json)

        pages = self.get_pages(
            account_id=account_id,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
        )
        if not pages:
            return None
        return OcrDocument(
            source_file=str(document_record.get("file_name") or ""),
            source_path=str(document_record.get("source_path") or ""),
            ocr_engine=str(document_record.get("ocr_engine") or ""),
            pages=[
                OcrPage(
                    page_number=int(page.get("page_number") or 0),
                    text=str(page.get("text") or ""),
                    blocks=[
                        OcrBlock(
                            block_id=str(block.get("block_id") or ""),
                            text=str(block.get("text") or ""),
                            page_number=int(block.get("page_number") or page.get("page_number") or 0),
                            confidence=block.get("confidence"),
                            metadata=block.get("metadata") or {},
                        )
                        for block in page.get("blocks", [])
                        if isinstance(block, dict)
                    ],
                )
                for page in pages
            ],
            metadata=dict(document_record.get("metadata") or {}),
        )

    def _ensure_index(self, index_name: str, mapping: dict[str, Any]) -> None:
        if not self.client.indices.exists(index=index_name):
            self.client.indices.create(index=index_name, body=mapping)

    def _bulk_index(self, actions: list[dict[str, Any]]) -> None:
        if not actions:
            return
        from elasticsearch import helpers

        helpers.bulk(self.client, actions)

    def _get_source_by_id(self, index_name: str, document_id: str) -> dict[str, Any] | None:
        try:
            response = self.client.get(index=index_name, id=document_id)
        except Exception as exc:
            if _is_not_found_error(exc):
                return None
            raise
        if response.get("found") is False:
            return None
        source = response.get("_source")
        return source if isinstance(source, dict) else None

    def _page_actions(
        self,
        account_id: str,
        knowledge_base_id: str,
        document_id: str,
        document: OcrDocument,
        now: str,
    ) -> list[dict[str, Any]]:
        actions = []
        for page in document.pages:
            page_id = f"{document_id}_p{page.page_number:04d}"
            actions.append(
                {
                    "_op_type": "index",
                    "_index": self.pages_index,
                    "_id": page_id,
                    "_source": {
                        "account_id": account_id,
                        "knowledge_base_id": knowledge_base_id,
                        "document_id": document_id,
                        "page_id": page_id,
                        "page_number": page.page_number,
                        "text": page.text,
                        "markdown": page.text,
                        "blocks": [
                            {
                                "block_id": block.block_id,
                                "text": block.text,
                                "page_number": block.page_number,
                                "confidence": block.confidence,
                                "metadata": block.metadata,
                            }
                            for block in page.blocks
                        ],
                        "created_at": now,
                    },
                }
            )
        return actions

    def _chunk_actions(
        self,
        account_id: str,
        knowledge_base_id: str,
        document_id: str,
        document: OcrDocument,
        chunk_pages: int,
        now: str,
    ) -> list[dict[str, Any]]:
        actions = []
        for index, chunk in enumerate(chunk_by_pages(document, pages_per_chunk=chunk_pages), start=1):
            chunk_id = f"{document_id}_c{index:04d}"
            actions.append(
                {
                    "_op_type": "index",
                    "_index": self.chunks_index,
                    "_id": chunk_id,
                    "_source": {
                        "account_id": account_id,
                        "knowledge_base_id": knowledge_base_id,
                        "document_id": document_id,
                        "chunk_id": chunk_id,
                        "chunk_index": index,
                        "page_start": chunk.page_start,
                        "page_end": chunk.page_end,
                        "text": chunk.text,
                        "token_count": len(chunk.text),
                        "chunk_strategy": "page_window",
                        "chunk_version": "v1",
                        "embedding_model": None,
                        "embedding": None,
                        "created_at": now,
                    },
                }
            )
        return actions

    def _document_mapping(self) -> dict[str, Any]:
        return {
            "mappings": {
                "properties": {
                    "account_id": {"type": "keyword"},
                    "knowledge_base_id": {"type": "keyword"},
                    "document_id": {"type": "keyword"},
                    "file_name": _text_keyword(self.analyzer, self.search_analyzer),
                    "file_ext": {"type": "keyword"},
                    "mime_type": {"type": "keyword"},
                    "file_size": {"type": "long"},
                    "file_sha256": {"type": "keyword"},
                    "status": {"type": "keyword"},
                    "source": {"type": "keyword"},
                    "source_path": {"type": "keyword"},
                    "ocr_engine": {"type": "keyword"},
                    "ocr_options_hash": {"type": "keyword"},
                    "pipeline_version": {"type": "keyword"},
                    "processing_fingerprint": {"type": "keyword"},
                    "page_count": {"type": "integer"},
                    "language": {"type": "keyword"},
                    "title": _text_keyword(self.analyzer, self.search_analyzer),
                    "tags": {"type": "keyword"},
                    "metadata": {"type": "object", "enabled": False},
                    "markdown": {"type": "text", "analyzer": self.analyzer, "search_analyzer": self.search_analyzer},
                    "ocr_json": {"type": "object", "enabled": False},
                    "created_at": {"type": "date"},
                    "updated_at": {"type": "date"},
                    "processed_at": {"type": "date"},
                }
            }
        }

    def _page_mapping(self) -> dict[str, Any]:
        return {
            "mappings": {
                "properties": {
                    "account_id": {"type": "keyword"},
                    "knowledge_base_id": {"type": "keyword"},
                    "document_id": {"type": "keyword"},
                    "page_id": {"type": "keyword"},
                    "page_number": {"type": "integer"},
                    "text": {"type": "text", "analyzer": self.analyzer, "search_analyzer": self.search_analyzer},
                    "markdown": {"type": "text", "analyzer": self.analyzer, "search_analyzer": self.search_analyzer},
                    "blocks": {"type": "object", "enabled": False},
                    "created_at": {"type": "date"},
                }
            }
        }

    def _chunk_mapping(self) -> dict[str, Any]:
        return {
            "mappings": {
                "properties": {
                    "account_id": {"type": "keyword"},
                    "knowledge_base_id": {"type": "keyword"},
                    "document_id": {"type": "keyword"},
                    "chunk_id": {"type": "keyword"},
                    "chunk_index": {"type": "integer"},
                    "page_start": {"type": "integer"},
                    "page_end": {"type": "integer"},
                    "text": {"type": "text", "analyzer": self.analyzer, "search_analyzer": self.search_analyzer},
                    "token_count": {"type": "integer"},
                    "chunk_strategy": {"type": "keyword"},
                    "chunk_version": {"type": "keyword"},
                    "embedding_model": {"type": "keyword"},
                    "created_at": {"type": "date"},
                }
            }
        }

    def _job_mapping(self) -> dict[str, Any]:
        return {
            "mappings": {
                "properties": {
                    "account_id": {"type": "keyword"},
                    "knowledge_base_id": {"type": "keyword"},
                    "job_id": {"type": "keyword"},
                    "document_id": {"type": "keyword"},
                    "batch_id": {"type": "keyword"},
                    "file_name": _text_keyword(self.analyzer, self.search_analyzer),
                    "type": {"type": "keyword"},
                    "state": {"type": "keyword"},
                    "progress": {"type": "integer"},
                    "message": {"type": "text", "analyzer": self.analyzer, "search_analyzer": self.search_analyzer},
                    "error": {"type": "text"},
                    "engine": {"type": "keyword"},
                    "reused": {"type": "boolean"},
                    "queue_index": {"type": "integer"},
                    "queue_total": {"type": "integer"},
                    "total_pages": {"type": "integer"},
                    "created_at": {"type": "date"},
                    "updated_at": {"type": "date"},
                    "finished_at": {"type": "date"},
                }
            }
        }

    def _llm_conversation_mapping(self) -> dict[str, Any]:
        return {
            "mappings": {
                "properties": {
                    "conversation_id": {"type": "keyword"},
                    "account_id": {"type": "keyword"},
                    "knowledge_base_id": {"type": "keyword"},
                    "title": _text_keyword(self.analyzer, self.search_analyzer),
                    "origin": {"type": "keyword"},
                    "context_mode": {"type": "keyword"},
                    "context_document_ids": {"type": "keyword"},
                    "context_documents": {"type": "object", "enabled": False},
                    "system_prompt": {"type": "text", "analyzer": self.analyzer, "search_analyzer": self.search_analyzer},
                    "provider": {"type": "keyword"},
                    "model": {"type": "keyword"},
                    "temperature": {"type": "float"},
                    "max_tokens": {"type": "integer"},
                    "message_count": {"type": "integer"},
                    "status": {"type": "keyword"},
                    "metadata": {"type": "object", "enabled": False},
                    "created_at": {"type": "date"},
                    "updated_at": {"type": "date"},
                    "deleted_at": {"type": "date"},
                }
            }
        }

    def _llm_message_mapping(self) -> dict[str, Any]:
        return {
            "mappings": {
                "properties": {
                    "message_id": {"type": "keyword"},
                    "conversation_id": {"type": "keyword"},
                    "account_id": {"type": "keyword"},
                    "knowledge_base_id": {"type": "keyword"},
                    "role": {"type": "keyword"},
                    "content": {"type": "text", "analyzer": self.analyzer, "search_analyzer": self.search_analyzer},
                    "sequence": {"type": "integer"},
                    "created_at": {"type": "date"},
                    "metadata": {"type": "object", "enabled": False},
                }
            }
        }


def options_hash(options: dict[str, Any]) -> str:
    encoded = json.dumps(_json_safe(options), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _text_keyword(analyzer: str, search_analyzer: str) -> dict[str, Any]:
    return {
        "type": "text",
        "analyzer": analyzer,
        "search_analyzer": search_analyzer,
        "fields": {"keyword": {"type": "keyword", "ignore_above": 512}},
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _non_empty_str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _int_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(_int_or_default(value, default), maximum))


def _append_start_sequence(payload: dict[str, Any], conversation: dict[str, Any]) -> int:
    start_sequence = payload.get("start_sequence")
    if start_sequence is not None and start_sequence != "":
        return max(0, int(start_sequence) - 1)
    return max(0, _int_or_default(conversation.get("message_count"), 0))


def _llm_context_document_ids(record: dict[str, Any]) -> list[str]:
    document_ids: list[str] = []
    _append_unique_strings(document_ids, record.get("context_document_ids"))
    context_documents = record.get("context_documents")
    if isinstance(context_documents, list):
        for context_document in context_documents:
            if isinstance(context_document, dict):
                _append_unique_strings(document_ids, [context_document.get("document_id")])
    return document_ids


def _append_unique_strings(target: list[str], values: Any) -> None:
    if not isinstance(values, list):
        return
    for value in values:
        text = _non_empty_str(value)
        if text and text not in target:
            target.append(text)


def _llm_source_matches_scope(source: dict[str, Any], query: dict[str, Any]) -> bool:
    account_id = _non_empty_str(query.get("account_id"))
    if account_id and source.get("account_id") != account_id:
        return False
    knowledge_base_id = _non_empty_str(query.get("knowledge_base_id"))
    if knowledge_base_id and source.get("knowledge_base_id") != knowledge_base_id:
        return False
    return True


def _is_not_found_error(exc: Exception) -> bool:
    if getattr(exc, "status_code", None) == 404:
        return True
    meta = getattr(exc, "meta", None)
    if getattr(meta, "status", None) == 404:
        return True
    return exc.__class__.__name__ == "NotFoundError"


def _visible_account_ids(account_id: str) -> list[str]:
    account = str(account_id or "").strip() or DEFAULT_ACCOUNT_ID
    if account == PUBLIC_ACCOUNT_ID:
        return [PUBLIC_ACCOUNT_ID]
    return [account, PUBLIC_ACCOUNT_ID]
