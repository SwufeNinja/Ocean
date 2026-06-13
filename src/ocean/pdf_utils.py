from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader, PdfWriter


@dataclass(slots=True)
class PdfPart:
    path: Path
    page_start: int
    page_end: int


def count_pdf_pages(pdf_path: str | Path) -> int:
    reader = PdfReader(str(pdf_path))
    return len(reader.pages)


def split_pdf(pdf_path: str | Path, output_dir: str | Path, max_pages: int = 200) -> list[PdfPart]:
    pdf = Path(pdf_path).expanduser().resolve()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    reader = PdfReader(str(pdf))
    total_pages = len(reader.pages)
    if total_pages <= max_pages:
        return [PdfPart(path=pdf, page_start=1, page_end=total_pages)]

    parts: list[PdfPart] = []
    for start_index in range(0, total_pages, max_pages):
        end_index = min(start_index + max_pages, total_pages)
        writer = PdfWriter()
        for page_index in range(start_index, end_index):
            writer.add_page(reader.pages[page_index])

        page_start = start_index + 1
        page_end = end_index
        part_path = output / f"{pdf.stem}__part{len(parts) + 1:03d}_p{page_start:04d}-{page_end:04d}.pdf"
        with part_path.open("wb") as f:
            writer.write(f)
        parts.append(PdfPart(path=part_path, page_start=page_start, page_end=page_end))
    return parts
