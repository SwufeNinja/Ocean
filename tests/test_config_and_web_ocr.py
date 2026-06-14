from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ocean.config import load_config
from ocean.web import _web_ocr_config


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


if __name__ == "__main__":
    unittest.main()
