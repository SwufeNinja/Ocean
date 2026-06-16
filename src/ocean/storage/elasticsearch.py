from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ocean.extractors import chunk_by_pages
from ocean.models import OcrBlock, OcrDocument, OcrPage

PIPELINE_VERSION = "ocean-ocr-cache-v1"
DEFAULT_ACCOUNT_ID = "local"
DEFAULT_KNOWLEDGE_BASE_ID = "default"


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

    def ensure_indices(self) -> None:
        self._ensure_index(self.documents_index, self._document_mapping())
        self._ensure_index(self.pages_index, self._page_mapping())
        self._ensure_index(self.chunks_index, self._chunk_mapping())
        self._ensure_index(self.jobs_index, self._job_mapping())

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
                        {"term": {"account_id": account_id}},
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
            {"term": {"account_id": account_id}},
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
                        {"term": {"account_id": account_id}},
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
                    "file_name": _text_keyword(self.analyzer, self.search_analyzer),
                    "type": {"type": "keyword"},
                    "state": {"type": "keyword"},
                    "progress": {"type": "integer"},
                    "message": {"type": "text", "analyzer": self.analyzer, "search_analyzer": self.search_analyzer},
                    "error": {"type": "text"},
                    "engine": {"type": "keyword"},
                    "reused": {"type": "boolean"},
                    "created_at": {"type": "date"},
                    "updated_at": {"type": "date"},
                    "finished_at": {"type": "date"},
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
