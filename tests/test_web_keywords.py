from __future__ import annotations

import unittest
from pathlib import Path

from ocean.models import ExtractionResult
from ocean.web import _keyword_results_markdown, _parse_keywords, make_app


class WebKeywordExtractionTest(unittest.TestCase):
    def test_keyword_route_is_registered(self) -> None:
        app = make_app(config={}, output_dir="outputs_test_web")
        routes = {route.path for route in app.routes}

        self.assertIn("/api/jobs/{job_id}/extract-keywords", routes)
        self.assertIn("/api/jobs/batch", routes)

    def test_keyword_panel_is_present_in_vue_app(self) -> None:
        app_vue = Path("frontend/src/App.vue").read_text(encoding="utf-8")

        self.assertIn("\u5173\u952e\u8bcd\u6bb5\u843d\u63d0\u53d6", app_vue)
        self.assertIn("\u63d0\u53d6\u7c92\u5ea6", app_vue)
        self.assertIn("\u4f7f\u7528\u6b63\u5219", app_vue)
        self.assertIn("\u533a\u5206\u5927\u5c0f\u5199", app_vue)
        self.assertIn("\u7b80\u7e41\u8f6c\u6362\u5339\u914d", app_vue)
        self.assertIn("\u53bb\u91cd\u5408\u5e76", app_vue)
        self.assertIn("\u8bf7\u8f93\u5165\u5173\u952e\u8bcd...", app_vue)
        self.assertIn('const keywordInput = ref("")', app_vue)
        self.assertNotIn("Keyword Extractor", app_vue)
        self.assertNotIn("keywordInput: '\u9752\u5e74", app_vue)
        self.assertIn("extractKeywords", app_vue)
        self.assertIn("createJobs", app_vue)
        self.assertIn("folderInput", app_vue)
        self.assertIn("pageItems.value = buildKeywordPageItems(data)", app_vue)
        self.assertIn('keywordLabel: (item.matched_keywords || []).join("\u3001")', app_vue)
        self.assertIn("sourcePageLabel: pageLabel(item)", app_vue)
        self.assertIn("currentKeywordLabel", app_vue)
        self.assertIn("currentSourcePageLabel", app_vue)
        self.assertNotIn("\u547d\u4e2d\u9875\u9762 ${index}", app_vue)
        self.assertNotIn("`- \u547d\u4e2d\u5173\u952e\u8bcd\uff1a${(item.matched_keywords", app_vue)
        self.assertIn("\u5173\u952e\u8bcd\u63d0\u53d6\u7ed3\u679c", app_vue)

    def test_parse_keywords_accepts_common_separators(self) -> None:
        keywords = _parse_keywords("youth,league\nyouth work\uff1byouth policy")

        self.assertEqual(keywords, ["youth", "league", "youth work", "youth policy"])

    def test_keyword_results_markdown_is_readable(self) -> None:
        markdown = _keyword_results_markdown(
            [
                ExtractionResult(
                    result_id="K0001",
                    source_file="sample.pdf",
                    page_start=2,
                    page_end=3,
                    extraction_method="keyword",
                    matched_keywords=["youth"],
                    text="youth work original text",
                )
            ],
            ["youth"],
            "any",
        )

        self.assertIn("# \u5173\u952e\u8bcd\u63d0\u53d6\u7ed3\u679c", markdown)
        self.assertIn("- \u9875\u7801\uff1a\u7b2c 2-3 \u9875", markdown)
        self.assertIn("youth work original text", markdown)


if __name__ == "__main__":
    unittest.main()
