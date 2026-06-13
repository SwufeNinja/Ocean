from __future__ import annotations

import csv
from pathlib import Path

from ocean.models import ExtractionResult

_FIELDNAMES = [
    "result_id",
    "source_file",
    "page_start",
    "page_end",
    "extraction_method",
    "matched_keywords",
    "topic",
    "relevance",
    "reason",
    "text",
]


def write_extraction_csv(results: list[ExtractionResult], output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELDNAMES)
        writer.writeheader()
        for result in results:
            row = result.to_dict()
            row["matched_keywords"] = ";".join(result.matched_keywords)
            writer.writerow({key: row.get(key, "") for key in _FIELDNAMES})
