from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ocean.models import OcrDocument, OcrPage
from ocean.pipeline import run_ocr


class FakeClient:
    def __init__(self, engine: str) -> None:
        self.engine = engine


def fake_document(pdf: Path, engine: str) -> OcrDocument:
    return OcrDocument(
        source_file=pdf.name,
        source_path=str(pdf),
        ocr_engine=engine,
        pages=[OcrPage(page_number=1, text=f"{engine} text")],
    )


class RunOcrReliabilityTest(unittest.TestCase):
    def test_fallback_engine_is_used_and_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf = root / "sample.pdf"
            pdf.write_bytes(b"%PDF-1.4")
            output = root / "out"

            def create_client(config):
                return FakeClient(config["engine"])

            def recognize(client, pdf_path, _config):
                if client.engine == "primary":
                    raise RuntimeError("primary failed")
                return fake_document(pdf_path, client.engine)

            config = {"ocr": {"engine": "primary", "fallback_engine": "fallback"}}
            with (
                patch("ocean.pipeline.list_pdfs", return_value=[pdf]),
                patch("ocean.pipeline.count_pdf_pages", return_value=1),
                patch("ocean.pipeline.create_ocr_client", side_effect=create_client),
                patch("ocean.pipeline._recognize_pdf_with_split", side_effect=recognize),
            ):
                documents = run_ocr(root, output, config)

            report = json.loads((output / "ocr" / "run_report.json").read_text(encoding="utf-8"))
            self.assertEqual(len(documents), 1)
            self.assertEqual(report["status"], "success")
            self.assertEqual(report["success_count"], 1)
            self.assertEqual(report["failed_count"], 0)
            self.assertTrue(report["files"][0]["fallback_used"])
            self.assertEqual(report["files"][0]["ocr_engine"], "fallback")
            self.assertEqual([item["status"] for item in report["files"][0]["attempts"]], ["failed", "success"])

    def test_failed_file_does_not_stop_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_pdf = root / "first.pdf"
            second_pdf = root / "second.pdf"
            first_pdf.write_bytes(b"%PDF-1.4")
            second_pdf.write_bytes(b"%PDF-1.4")
            output = root / "out"

            def create_client(config):
                return FakeClient(config["engine"])

            def recognize(client, pdf_path, _config):
                if pdf_path.name == "first.pdf":
                    raise RuntimeError("broken pdf")
                return fake_document(pdf_path, client.engine)

            config = {"ocr": {"engine": "primary"}}
            with (
                patch("ocean.pipeline.list_pdfs", return_value=[first_pdf, second_pdf]),
                patch("ocean.pipeline.count_pdf_pages", return_value=1),
                patch("ocean.pipeline.create_ocr_client", side_effect=create_client),
                patch("ocean.pipeline._recognize_pdf_with_split", side_effect=recognize),
            ):
                documents = run_ocr(root, output, config)

            report = json.loads((output / "ocr" / "run_report.json").read_text(encoding="utf-8"))
            self.assertEqual(len(documents), 1)
            self.assertEqual(report["status"], "partial_success")
            self.assertEqual(report["success_count"], 1)
            self.assertEqual(report["failed_count"], 1)
            self.assertEqual([item["status"] for item in report["files"]], ["failed", "success"])
            self.assertIn("broken pdf", report["files"][0]["error"])
            self.assertTrue((output / "ocr" / "second.json").exists())
            self.assertTrue((output / "ocr" / "second.md").exists())


if __name__ == "__main__":
    unittest.main()
