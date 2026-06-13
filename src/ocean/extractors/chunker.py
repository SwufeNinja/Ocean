from __future__ import annotations

from ocean.models import OcrDocument, TextChunk


def chunk_by_pages(document: OcrDocument, pages_per_chunk: int = 3) -> list[TextChunk]:
    pages_per_chunk = max(1, pages_per_chunk)
    chunks: list[TextChunk] = []
    pages = document.pages
    for index in range(0, len(pages), pages_per_chunk):
        group = pages[index : index + pages_per_chunk]
        if not group:
            continue
        page_start = group[0].page_number
        page_end = group[-1].page_number
        text = "\n\n".join(f"[Page {page.page_number}]\n{page.text}" for page in group if page.text.strip())
        chunks.append(
            TextChunk(
                chunk_id=f"{document.source_file}_p{page_start}_p{page_end}",
                source_file=document.source_file,
                page_start=page_start,
                page_end=page_end,
                text=text,
            )
        )
    return chunks
