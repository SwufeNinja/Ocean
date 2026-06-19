from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ocean.exporters import write_ocr_markdown
from ocean.models import OcrDocument, OcrPage


class MarkdownExporterTest(unittest.TestCase):
    def test_ocr_markdown_includes_page_headings(self) -> None:
        document = OcrDocument(
            source_file="sample.pdf",
            source_path="/tmp/sample.pdf",
            ocr_engine="mineru",
            pages=[
                OcrPage(page_number=1, text="第一页内容"),
                OcrPage(page_number=2, text=""),
                OcrPage(page_number=3, text="第三页内容"),
            ],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "sample.md"
            write_ocr_markdown(document, output)
            markdown = output.read_text(encoding="utf-8")

        self.assertIn("<!-- source_file: sample.pdf -->", markdown)
        self.assertIn("## Page 1", markdown)
        self.assertIn("## Page 2", markdown)
        self.assertIn("## Page 3", markdown)
        self.assertLess(markdown.index("## Page 1"), markdown.index("第一页内容"))
        self.assertLess(markdown.index("## Page 3"), markdown.index("第三页内容"))


if __name__ == "__main__":
    unittest.main()
