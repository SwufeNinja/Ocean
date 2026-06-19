from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ocean.extractors.semantic import extract_semantic
from ocean.models import OcrDocument, OcrPage, TextChunk
from ocean.pipeline import run_semantic_extraction


class FakeLlmClient:
    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, options=None) -> str:
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("temporary chunk failure")
        return json.dumps(
            {
                "items": [
                    {
                        "source_file": "sample.pdf",
                        "page_start": self.calls,
                        "page_end": self.calls,
                        "relevance": "high",
                        "reason": "matched topic",
                        "text": f"chunk {self.calls} text",
                    }
                ]
            },
            ensure_ascii=False,
        )


class SemanticExtractorTest(unittest.TestCase):
    def test_chunk_failure_is_recorded_and_does_not_stop_extraction(self) -> None:
        chunks = [
            TextChunk("sample_p1", "sample.pdf", 1, 1, "page 1"),
            TextChunk("sample_p2", "sample.pdf", 2, 2, "page 2"),
            TextChunk("sample_p3", "sample.pdf", 3, 3, "page 3"),
        ]
        failures: list[dict[str, object]] = []

        results = extract_semantic(
            chunks,
            [{"name": "topic", "description": "description"}],
            FakeLlmClient(),  # type: ignore[arg-type]
            failure_callback=failures.append,
        )

        self.assertEqual([result.text for result in results], ["chunk 1 text", "chunk 3 text"])
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["chunk_id"], "sample_p2")
        self.assertIn("temporary chunk failure", str(failures[0]["error"]))

    def test_pipeline_writes_semantic_failures_json(self) -> None:
        document = OcrDocument(
            source_file="sample.pdf",
            source_path="/tmp/sample.pdf",
            ocr_engine="mineru",
            pages=[
                OcrPage(page_number=1, text="page 1"),
                OcrPage(page_number=2, text="page 2"),
                OcrPage(page_number=3, text="page 3"),
            ],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ocr_dir = root / "ocr"
            ocr_dir.mkdir()
            (ocr_dir / "sample.json").write_text(json.dumps(document.to_dict(), ensure_ascii=False), encoding="utf-8")

            with patch("ocean.pipeline.OpenAICompatibleClient.from_config", return_value=FakeLlmClient()):
                results = run_semantic_extraction(
                    ocr_dir,
                    root / "out",
                    {
                        "extraction": {
                            "chunk_pages": 1,
                            "semantic_topics": [{"name": "topic", "description": "description"}],
                        },
                        "llm": {"api_base_url": "x", "api_key": "x", "model": "x"},
                    },
                )

            failures = json.loads((root / "out" / "extract" / "semantic_failures.json").read_text(encoding="utf-8"))

        self.assertEqual(len(results), 2)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["page_start"], 2)


if __name__ == "__main__":
    unittest.main()
