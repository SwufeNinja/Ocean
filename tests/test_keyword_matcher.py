from __future__ import annotations

import unittest

from ocean.extractors.keywords import extract_keywords
from ocean.extractors.matcher import KeywordMatcher, KeywordMatchOptions, match_keywords
from ocean.models import OcrDocument, OcrPage


class KeywordMatcherTest(unittest.TestCase):
    def test_any_match_uses_containment(self) -> None:
        match = match_keywords("youth policy and organization", ["youth", "league"], match_mode="any")

        self.assertTrue(match.is_match)
        self.assertEqual(match.matched_keywords, ["youth"])

    def test_all_match_requires_every_keyword(self) -> None:
        matcher = KeywordMatcher(["youth", "league"], KeywordMatchOptions(match_mode="all"))

        self.assertFalse(matcher.match("only youth appears").is_match)
        self.assertTrue(matcher.match("youth league work").is_match)

    def test_case_insensitive_match(self) -> None:
        match = match_keywords("Youth policy", ["youth"], case_sensitive=False)

        self.assertTrue(match.is_match)
        self.assertEqual(match.matched_keywords, ["youth"])

    def test_regex_match(self) -> None:
        match = match_keywords("youth cadre training", [r"youth.*training"], use_regex=True)

        self.assertTrue(match.is_match)
        self.assertEqual(match.matched_keywords, [r"youth.*training"])


    def test_normalize_chinese_matches_traditional_text(self) -> None:
        match = match_keywords("????????", ["??????"], normalize_chinese_text=True)

        self.assertTrue(match.is_match)
        self.assertEqual(match.matched_keywords, ["??????"])

    def test_deduplicate_merges_overlapping_context(self) -> None:
        document = OcrDocument(
            source_file="sample.pdf",
            source_path="sample.pdf",
            ocr_engine="test",
            pages=[OcrPage(page_number=1, text="alpha youth\n\nbeta league\n\ngamma")],
        )

        results = extract_keywords(
            document,
            ["youth", "league"],
            context_before=1,
            context_after=1,
            deduplicate=True,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].matched_keywords, ["league", "youth"])
        self.assertIn("merged_result_ids", results[0].metadata)

    def test_empty_keywords_do_not_match(self) -> None:
        match = match_keywords("youth", ["", "  "], match_mode="any")

        self.assertFalse(match.is_match)
        self.assertEqual(match.matched_keywords, [])

    def test_page_granularity_extracts_whole_page(self) -> None:
        document = OcrDocument(
            source_file="sample.pdf",
            source_path="sample.pdf",
            ocr_engine="test",
            pages=[
                OcrPage(page_number=1, text="unrelated"),
                OcrPage(page_number=2, text="first paragraph\n\nyouth work\n\nthird paragraph"),
            ],
        )

        results = extract_keywords(document, ["youth"], granularity="page")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].page_start, 2)
        self.assertEqual(results[0].metadata["granularity"], "page")
        self.assertIn("third paragraph", results[0].text)
        self.assertTrue(results[0].created_at)


if __name__ == "__main__":
    unittest.main()
