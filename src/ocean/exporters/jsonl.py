from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ocean.models import ExtractionResult, OcrDocument


def write_ocr_json(document: OcrDocument, output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def read_ocr_json(input_path: str | Path) -> OcrDocument:
    from ocean.models import ocr_document_from_dict

    data: dict[str, Any] = json.loads(Path(input_path).read_text(encoding="utf-8"))
    return ocr_document_from_dict(data)


def write_extraction_json(results: list[ExtractionResult], output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [result.to_dict() for result in results]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
