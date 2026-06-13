from __future__ import annotations

from typing import Any

from ocean.ocr.base import OcrClient
from ocean.ocr.mineru import MineruClient
from ocean.ocr.paddle import PaddleOcrClient


def create_ocr_client(config: dict[str, Any]) -> OcrClient:
    engine = (config.get("engine") or "").lower()
    api_base_url = config.get("api_base_url", "")
    api_key = config.get("api_token") or config.get("api_key", "")
    if engine in {"paddle", "paddleocr"}:
        return PaddleOcrClient(api_base_url=api_base_url, api_key=api_key)
    if engine in {"mineru", "mineruocr"}:
        return MineruClient(api_base_url=api_base_url or "https://mineru.net", api_token=api_key)
    raise ValueError(f"Unsupported OCR engine: {engine}")
