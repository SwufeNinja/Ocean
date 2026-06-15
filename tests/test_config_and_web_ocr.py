from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ocean.config import load_config
from ocean.web import _create_web_job_from_upload, _is_pdf_upload, _normalize_engine, _web_ocr_config, make_app


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
        self.assertTrue(_is_pdf_upload(FakeUpload("x.PDF", b"")))  # type: ignore[arg-type]
        self.assertFalse(_is_pdf_upload(FakeUpload("x.txt", b"")))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
