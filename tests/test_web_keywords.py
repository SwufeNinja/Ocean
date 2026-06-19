from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi import HTTPException

from ocean.models import ExtractionResult
from ocean.web import _keyword_results_markdown, _parse_keywords, make_app


class WebKeywordExtractionTest(unittest.TestCase):
    def test_keyword_route_is_registered(self) -> None:
        app = make_app(config={}, output_dir="outputs_test_web")
        routes = {route.path for route in app.routes}

        self.assertIn("/api/jobs/{job_id}/extract-keywords", routes)
        self.assertIn("/api/jobs/batch", routes)
        self.assertIn("/api/jobs", routes)
        self.assertIn("/api/jobs/{job_id}", routes)
        self.assertIn("/api/documents", routes)
        self.assertIn("/api/documents/{document_id}/markdown", routes)
        self.assertIn("/api/documents/{document_id}/pages", routes)
        self.assertIn("/api/documents/{document_id}/extract-keywords", routes)

    def test_document_job_id_returns_document_status(self) -> None:
        store = FakeDocumentStore()
        with patch("ocean.web.create_document_store", return_value=store):
            app = make_app(config={"elasticsearch": {"enabled": True}}, output_dir="outputs_test_web")

        login = _route_endpoint(app, "/api/auth/login", "POST")
        get_job = _route_endpoint(app, "/api/jobs/{job_id}", "GET")
        token = login({"username": "local", "password": "local"})["access_token"]

        data = get_job("document:doc1", authorization=f"Bearer {token}")

        self.assertEqual(data["job_id"], "document:doc1")
        self.assertEqual(data["document_id"], "doc1")
        self.assertEqual(data["state"], "done")
        self.assertEqual(data["progress"], 100)
        self.assertIn("/api/documents/doc1/markdown", data["markdown_url"])

        with self.assertRaises(HTTPException) as missing:
            get_job("document:missing", authorization=f"Bearer {token}")
        self.assertEqual(missing.exception.status_code, 404)

    def test_list_jobs_ignores_orphaned_persisted_active_jobs(self) -> None:
        store = FakeDocumentStore()
        with patch("ocean.web.create_document_store", return_value=store):
            app = make_app(config={"elasticsearch": {"enabled": True}}, output_dir="outputs_test_web")

        login = _route_endpoint(app, "/api/auth/login", "POST")
        list_jobs = _route_endpoint(app, "/api/jobs", "GET")
        token = login({"username": "local", "password": "local"})["access_token"]

        data = list_jobs(state="queued,running", authorization=f"Bearer {token}")

        self.assertEqual(data["count"], 0)
        self.assertEqual(data["jobs"], [])

    def test_get_orphaned_persisted_active_job_returns_failed(self) -> None:
        store = FakeDocumentStore()
        with patch("ocean.web.create_document_store", return_value=store):
            app = make_app(config={"elasticsearch": {"enabled": True}}, output_dir="outputs_test_web")

        login = _route_endpoint(app, "/api/auth/login", "POST")
        get_job = _route_endpoint(app, "/api/jobs/{job_id}", "GET")
        token = login({"username": "local", "password": "local"})["access_token"]

        data = get_job("job_running", authorization=f"Bearer {token}")

        self.assertEqual(data["job_id"], "job_running")
        self.assertEqual(data["state"], "failed")
        self.assertEqual(data["progress"], 100)
        self.assertEqual(data["error"], "任务不存在或已取消")
        self.assertEqual(store.saved_jobs[-1]["job_id"], "job_running")
        self.assertEqual(store.saved_jobs[-1]["state"], "failed")

    def test_keyword_panel_is_present_in_vue_app(self) -> None:
        app_vue = Path("frontend/src/App.vue").read_text(encoding="utf-8")

        self.assertIn("\u5173\u952e\u8bcd\u6bb5\u843d\u63d0\u53d6", app_vue)
        self.assertIn("\u63d0\u53d6\u7c92\u5ea6", app_vue)
        self.assertIn("\u4f7f\u7528\u6b63\u5219", app_vue)
        self.assertIn("\u533a\u5206\u5927\u5c0f\u5199", app_vue)
        self.assertIn("\u7b80\u7e41\u8f6c\u6362\u5339\u914d", app_vue)
        self.assertIn("\u53bb\u91cd\u5408\u5e76", app_vue)
        self.assertIn("\u8bf7\u8f93\u5165\u5173\u952e\u8bcd...", app_vue)
        self.assertIn('const keywordInput = ref("")', app_vue)
        self.assertNotIn("Keyword Extractor", app_vue)
        self.assertNotIn("keywordInput: '\u9752\u5e74", app_vue)
        self.assertIn("extractKeywords", app_vue)
        self.assertIn("listKnowledgeDocuments", app_vue)
        self.assertIn("openLibraryDocument", app_vue)
        self.assertIn("\u77e5\u8bc6\u5e93", app_vue)
        self.assertIn("createJobs", app_vue)
        self.assertIn("folderInput", app_vue)
        self.assertIn("pageItems.value = buildKeywordPageItems(data)", app_vue)
        self.assertIn('keywordLabel: (item.matched_keywords || []).join("\u3001")', app_vue)
        self.assertIn("sourcePageLabel: pageLabel(item)", app_vue)
        self.assertIn("currentKeywordLabel", app_vue)
        self.assertIn("currentSourcePageLabel", app_vue)
        self.assertNotIn("\u547d\u4e2d\u9875\u9762 ${index}", app_vue)
        self.assertNotIn("`- \u547d\u4e2d\u5173\u952e\u8bcd\uff1a${(item.matched_keywords", app_vue)
        self.assertIn("\u5173\u952e\u8bcd\u63d0\u53d6\u7ed3\u679c", app_vue)

    def test_parse_keywords_accepts_common_separators(self) -> None:
        keywords = _parse_keywords("youth,league\nyouth work\uff1byouth policy")

        self.assertEqual(keywords, ["youth", "league", "youth work", "youth policy"])

    def test_keyword_results_markdown_is_readable(self) -> None:
        markdown = _keyword_results_markdown(
            [
                ExtractionResult(
                    result_id="K0001",
                    source_file="sample.pdf",
                    page_start=2,
                    page_end=3,
                    extraction_method="keyword",
                    matched_keywords=["youth"],
                    text="youth work original text",
                )
            ],
            ["youth"],
            "any",
        )

        self.assertIn("# \u5173\u952e\u8bcd\u63d0\u53d6\u7ed3\u679c", markdown)
        self.assertIn("- \u9875\u7801\uff1a\u7b2c 2-3 \u9875", markdown)
        self.assertIn("youth work original text", markdown)


class FakeDocumentStore:
    def __init__(self) -> None:
        self.saved_jobs: list[dict[str, Any]] = []

    def get_job(
        self,
        *,
        account_id: str,
        knowledge_base_id: str,
        job_id: str,
    ) -> dict[str, Any] | None:
        for record in self.list_jobs(
            account_id=account_id,
            knowledge_base_id=knowledge_base_id,
            states=None,
            limit=100,
        ):
            if record["job_id"] == job_id:
                return record
        return None

    def save_job(self, job: dict[str, Any]) -> None:
        self.saved_jobs.append(job)

    def list_jobs(
        self,
        *,
        account_id: str,
        knowledge_base_id: str,
        states: list[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        records = [
            {
                "account_id": account_id,
                "knowledge_base_id": knowledge_base_id,
                "job_id": "job_running",
                "document_id": "doc_running",
                "file_name": "running.pdf",
                "state": "running",
                "progress": 45,
                "message": "processing",
                "engine": "mineru",
                "updated_at": "2026-06-17T00:02:00",
            },
            {
                "account_id": account_id,
                "knowledge_base_id": knowledge_base_id,
                "job_id": "job_queued",
                "document_id": "doc_queued",
                "file_name": "queued.pdf",
                "state": "queued",
                "progress": 1,
                "message": "queued",
                "engine": "mineru",
                "updated_at": "2026-06-17T00:01:00",
            },
            {
                "account_id": account_id,
                "knowledge_base_id": knowledge_base_id,
                "job_id": "job_done",
                "document_id": "doc1",
                "file_name": "done.pdf",
                "state": "done",
                "progress": 100,
                "message": "done",
                "engine": "mineru",
                "updated_at": "2026-06-17T00:00:00",
            },
        ]
        state_set = set(states or [])
        filtered = [record for record in records if not state_set or record["state"] in state_set]
        return filtered[:limit]

    def get_document(
        self,
        *,
        account_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> dict[str, Any] | None:
        if document_id != "doc1":
            return None
        return {
            "account_id": account_id,
            "knowledge_base_id": knowledge_base_id,
            "document_id": document_id,
            "file_name": "sample.pdf",
            "status": "done",
            "ocr_engine": "mineru",
            "page_count": 3,
            "created_at": "2026-06-17T00:00:00",
            "updated_at": "2026-06-17T00:00:00",
        }


def _route_endpoint(app: Any, path: str, method: str) -> Any:
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"Route not found: {method} {path}")


if __name__ == "__main__":
    unittest.main()
