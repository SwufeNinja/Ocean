from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from ocean.models import OcrDocument


class OcrClient(ABC):
    @abstractmethod
    def recognize_pdf(self, pdf_path: str | Path, options: dict[str, Any] | None = None) -> OcrDocument:
        """Recognize a PDF and return normalized page-level OCR data."""
        raise NotImplementedError
