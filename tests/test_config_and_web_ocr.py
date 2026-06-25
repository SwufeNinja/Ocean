from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from ocean.config import load_config
from ocean.models import OcrDocument, OcrPage
from ocean.pdf_utils import PdfPart
from ocean.web import (
    WebJob,
    _create_web_job_from_upload,
    _is_pdf_upload,
    _normalize_engine,
    _recognize_job,
    _run_job,
    _web_ocr_config,
    make_app,
)


class FakeUpload:
    def __init__(self, filename: str, content: bytes) -> None:
        self.filename = filename
        self._content = content
        self._offset = 0

    async def read(self, size: int = -1) -> bytes:
        if self._offset >= len(self._content):
            return b""
        if size is None or size < 0:
            size = len(self._content) - self._offset
        chunk = self._content[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


class ConfigAndWebOcrTest(unittest.TestCase):
    def test_load_config_reads_dotenv_with_utf8_bom(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".env").write_text("\ufeffOCEAN_TEST_BOM_TOKEN=secret\n", encoding="utf-8")
            (root / "config.yaml").write_text(
                "ocr:\n  api_token: ${OCEAN_TEST_BOM_TOKEN}\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"OCEAN_TEST_BOM_TOKEN": ""}, clear=False):
                os.environ.pop("OCEAN_TEST_BOM_TOKEN", None)
                config = load_config(root / "config.yaml")

        self.assertEqual(config["ocr"]["api_token"], "secret")

    def test_load_config_prefers_config_dir_dotenv_over_cwd_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cwd = root / "cwd"
            config_dir = root / "config"
            cwd.mkdir()
            config_dir.mkdir()
            (cwd / ".env").write_text("OCEAN_TEST_PRIORITY_TOKEN=cwd\n", encoding="utf-8")
            (config_dir / ".env").write_text("OCEAN_TEST_PRIORITY_TOKEN=config\n", encoding="utf-8")
            (config_dir / "config.yaml").write_text(
                "ocr:\n  api_token: ${OCEAN_TEST_PRIORITY_TOKEN}\n",
                encoding="utf-8",
            )

            old_cwd = Path.cwd()
            try:
                os.chdir(cwd)
                with patch.dict(os.environ, {}, clear=False):
                    os.environ.pop("OCEAN_TEST_PRIORITY_TOKEN", None)
                    config = load_config(config_dir / "config.yaml")
            finally:
                os.chdir(old_cwd)

        self.assertEqual(config["ocr"]["api_token"], "config")

    def test_load_config_config_dir_dotenv_is_not_polluted_by_prior_cwd_load(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cwd = root / "cwd"
            config_dir = root / "config"
            cwd.mkdir()
            config_dir.mkdir()
            (cwd / ".env").write_text("OCEAN_TEST_POLLUTION_TOKEN=cwd\n", encoding="utf-8")
            (cwd / "config.yaml").write_text(
                "ocr:\n  api_token: ${OCEAN_TEST_POLLUTION_TOKEN}\n",
                encoding="utf-8",
            )
            (config_dir / ".env").write_text("OCEAN_TEST_POLLUTION_TOKEN=config\n", encoding="utf-8")
            (config_dir / "config.yaml").write_text(
                "ocr:\n  api_token: ${OCEAN_TEST_POLLUTION_TOKEN}\n",
                encoding="utf-8",
            )

            old_cwd = Path.cwd()
            try:
                os.chdir(cwd)
                with patch.dict(os.environ, {}, clear=False):
                    os.environ.pop("OCEAN_TEST_POLLUTION_TOKEN", None)
                    first_config = load_config(cwd / "config.yaml")
                    second_config = load_config(config_dir / "config.yaml")
            finally:
                os.chdir(old_cwd)

        self.assertEqual(first_config["ocr"]["api_token"], "cwd")
        self.assertEqual(second_config["ocr"]["api_token"], "config")

    def test_web_mineru_config_does_not_inherit_paddle_options_when_switching(self) -> None:
        config = {
            "ocr": {
                "engine": "paddleocr",
                "api_base_url": "https://paddle.example/api",
                "api_token": "paddle-token",
                "options": {
                    "model": "PaddleOCR-VL-1.6",
                    "max_pages_per_file": 50,
                },
            }
        }

        with patch.dict(os.environ, {"MINERU_API_TOKEN": "mineru-token"}, clear=False):
            mineru_config = _web_ocr_config(config, "mineru")

        self.assertEqual(mineru_config["api_base_url"], "https://mineru.net")
        self.assertEqual(mineru_config["api_token"], "mineru-token")
        self.assertEqual(mineru_config["options"]["max_pages_per_file"], 200)
        self.assertNotIn("model", mineru_config["options"])

    def test_web_default_engine_is_mineru(self) -> None:
        app = make_app(config={}, output_dir="outputs_test_web")
        endpoint = next(route.endpoint for route in app.routes if route.path == "/api/engines")

        self.assertEqual(endpoint()["default_engine"], "mineru")
        self.assertEqual(_normalize_engine(None), "mineru")

    def test_batch_upload_job_records_queue_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            upload = FakeUpload("folder/sample.pdf", b"%PDF-1.4 test")
            job = asyncio.run(
                _create_web_job_from_upload(
                    upload,  # type: ignore[arg-type]
                    "mineru",
                    root / "jobs",
                    root / "uploads",
                    batch_id="batch",
                    queue_index=2,
                    queue_total=3,
                )
            )

            self.assertEqual(job.batch_id, "batch")
            self.assertEqual(job.queue_index, 2)
            self.assertEqual(job.queue_total, 3)
            self.assertTrue(job.input_path.name.endswith(".pdf"))
            self.assertEqual(job.input_path.read_bytes(), b"%PDF-1.4 test")
            self.assertEqual(job.file_sha256, hashlib.sha256(b"%PDF-1.4 test").hexdigest())
            self.assertEqual(job.account_id, "local")
            self.assertEqual(job.knowledge_base_id, "default")
        self.assertTrue(_is_pdf_upload(FakeUpload("x.PDF", b"")))  # type: ignore[arg-type]
        self.assertFalse(_is_pdf_upload(FakeUpload("x.txt", b"")))  # type: ignore[arg-type]

    def test_web_ocr_uses_fallback_for_whole_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job = _fake_web_job(root)
            jobs = {job.job_id: job}
            store = FakeWebOcrDocumentStore()

            def create_client(config: dict[str, Any]) -> FakeOcrClient:
                return FakeOcrClient(str(config["engine"]))

            def recognize(client: FakeOcrClient, pdf_path: Path, _config: dict[str, Any]) -> OcrDocument:
                if client.engine == "mineru":
                    raise RuntimeError("primary failed")
                return _fake_document(pdf_path, client.engine)

            config = {
                "ocr": {
                    "engine": "mineru",
                    "fallback_engine": "paddleocr",
                    "options": {"max_pages_per_file": 10},
                }
            }
            with (
                patch("ocean.web.count_pdf_pages", return_value=1),
                patch("ocean.web.create_ocr_client", side_effect=create_client),
                patch("ocean.pipeline._recognize_pdf_with_split", side_effect=recognize),
            ):
                _recognize_job(job.job_id, jobs, threading.Lock(), config, store)  # type: ignore[arg-type]

        saved = store.saved_results[0]
        self.assertEqual(jobs[job.job_id].state, "done")
        self.assertEqual(saved["document"].ocr_engine, "paddleocr")
        self.assertTrue(saved["metadata"]["fallback_used"])
        self.assertEqual(
            [attempt["status"] for attempt in saved["metadata"]["ocr_attempts"]],
            ["failed", "success"],
        )

    def test_web_ocr_uses_fallback_for_split_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job = _fake_web_job(root)
            jobs = {job.job_id: job}
            store = FakeWebOcrDocumentStore()

            def create_client(config: dict[str, Any]) -> FakeOcrClient:
                return FakeOcrClient(str(config["engine"]))

            def fake_split(_pdf: Path, output_dir: str | Path, max_pages: int) -> list[PdfPart]:
                output = Path(output_dir)
                parts = []
                for index in range(1, 4):
                    part_path = output / f"part-{index}.pdf"
                    part_path.write_bytes(b"%PDF-1.4 part")
                    parts.append(PdfPart(path=part_path, page_start=index, page_end=index))
                return parts

            config = {
                "ocr": {
                    "engine": "mineru",
                    "fallback_engine": "paddleocr",
                    "options": {"max_pages_per_file": 1},
                }
            }
            with (
                patch("ocean.web.count_pdf_pages", return_value=3),
                patch("ocean.pipeline.count_pdf_pages", return_value=3),
                patch("ocean.pipeline.split_pdf", side_effect=fake_split),
                patch("ocean.web.create_ocr_client", side_effect=create_client),
            ):
                _recognize_job(job.job_id, jobs, threading.Lock(), config, store)  # type: ignore[arg-type]

        saved = store.saved_results[0]
        self.assertEqual(saved["document"].ocr_engine, "paddleocr")
        self.assertEqual([page.page_number for page in saved["document"].pages], [1, 2, 3])
        self.assertTrue(saved["metadata"]["fallback_used"])

    def test_web_ocr_failure_marks_processing_document_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job = _fake_web_job(root)
            jobs = {job.job_id: job}
            store = FakeWebOcrDocumentStore()

            def create_client(config: dict[str, Any]) -> FakeOcrClient:
                return FakeOcrClient(str(config["engine"]))

            def recognize(_client: FakeOcrClient, _pdf_path: Path, _config: dict[str, Any]) -> OcrDocument:
                raise RuntimeError("ocr exploded")

            config = {"ocr": {"engine": "mineru", "options": {"max_pages_per_file": 10}}}
            with (
                patch("ocean.web.count_pdf_pages", return_value=1),
                patch("ocean.web.create_ocr_client", side_effect=create_client),
                patch("ocean.pipeline._recognize_pdf_with_split", side_effect=recognize),
            ):
                _run_job(job.job_id, jobs, threading.Lock(), threading.Lock(), config, store)  # type: ignore[arg-type]

        document = store.documents[jobs[job.job_id].document_id or ""]
        self.assertEqual(jobs[job.job_id].state, "failed")
        self.assertEqual(document["status"], "failed")
        self.assertIn("ocr exploded", document["error"])
        self.assertNotEqual(store.processing_documents[-1]["status"], "processing")


class FakeOcrClient:
    def __init__(self, engine: str) -> None:
        self.engine = engine

    def recognize_pdf(self, pdf_path: Path, _options: dict[str, Any]) -> OcrDocument:
        if self.engine == "mineru":
            raise RuntimeError("primary failed")
        return _fake_document(pdf_path, self.engine)


class FakeWebOcrDocumentStore:
    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}
        self.processing_documents: list[dict[str, Any]] = []
        self.saved_jobs: list[dict[str, Any]] = []
        self.saved_results: list[dict[str, Any]] = []

    def find_processed_document(self, **_kwargs: Any) -> dict[str, Any] | None:
        return None

    def save_processing_document(self, document: dict[str, Any]) -> None:
        self.processing_documents.append(dict(document))
        self.documents[str(document["document_id"])] = dict(document)

    def save_job(self, job: dict[str, Any]) -> None:
        self.saved_jobs.append(dict(job))

    def save_ocr_result(self, **kwargs: Any) -> None:
        self.saved_results.append(dict(kwargs))
        self.documents[str(kwargs["document_id"])] = {
            "document_id": kwargs["document_id"],
            "status": "done",
            "ocr_engine": kwargs["document"].ocr_engine,
            "metadata": kwargs.get("metadata") or {},
        }


def _fake_web_job(root: Path, *, engine: str = "mineru") -> WebJob:
    job_dir = root / "job"
    job_dir.mkdir()
    input_path = root / "sample.pdf"
    input_path.write_bytes(b"%PDF-1.4")
    return WebJob(
        job_id="job-1",
        file_name="sample.pdf",
        engine=engine,
        job_dir=job_dir,
        input_path=input_path,
        log_path=job_dir / "ocr_run.log",
        created_at="2026-06-19T00:00:00+08:00",
        updated_at="2026-06-19T00:00:00+08:00",
    )


def _fake_document(pdf_path: Path, engine: str) -> OcrDocument:
    return OcrDocument(
        source_file=pdf_path.name,
        source_path=str(pdf_path),
        ocr_engine=engine,
        pages=[OcrPage(page_number=1, text=f"{engine} text")],
    )


if __name__ == "__main__":
    unittest.main()
