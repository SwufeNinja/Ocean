from __future__ import annotations

import re
from dataclasses import dataclass

from ocean.extractors.chinese import normalize_chinese


@dataclass(frozen=True, slots=True)
class KeywordMatchOptions:
    match_mode: str = "any"
    use_regex: bool = False
    case_sensitive: bool = True
    normalize_chinese: bool = False


@dataclass(frozen=True, slots=True)
class KeywordMatch:
    matched_keywords: list[str]
    is_match: bool


class KeywordMatcher:
    """Small extension point for keyword matching strategy improvements."""

    def __init__(self, keywords: list[str], options: KeywordMatchOptions | None = None) -> None:
        self.keywords = [keyword for keyword in keywords if keyword]
        self.options = options or KeywordMatchOptions()
        self._regex_flags = 0 if self.options.case_sensitive else re.IGNORECASE
        pattern_keywords = [_normalize(keyword, self.options.normalize_chinese) for keyword in self.keywords]
        self._regex_patterns = (
            [_compile_regex(keyword, self._regex_flags) for keyword in pattern_keywords]
            if self.options.use_regex
            else []
        )

    def match(self, text: str) -> KeywordMatch:
        haystack = _normalize(text, self.options.normalize_chinese)
        matched = self._match_regex(haystack) if self.options.use_regex else self._match_substrings(haystack)
        if self.options.match_mode == "all":
            is_match = len(matched) == len(self.keywords)
        else:
            is_match = bool(matched)
        return KeywordMatch(matched_keywords=matched, is_match=is_match)

    def _match_substrings(self, text: str) -> list[str]:
        if self.options.case_sensitive:
            return [
                keyword for keyword in self.keywords
                if _normalize(keyword, self.options.normalize_chinese) in text
            ]
        lowered_text = text.lower()
        return [
            keyword for keyword in self.keywords
            if _normalize(keyword, self.options.normalize_chinese).lower() in lowered_text
        ]

    def _match_regex(self, text: str) -> list[str]:
        matched: list[str] = []
        for keyword, pattern in zip(self.keywords, self._regex_patterns):
            if pattern.search(text):
                matched.append(keyword)
        return matched


def match_keywords(
    text: str,
    keywords: list[str],
    match_mode: str = "any",
    use_regex: bool = False,
    case_sensitive: bool = True,
    normalize_chinese_text: bool = False,
) -> KeywordMatch:
    matcher = KeywordMatcher(
        keywords,
        KeywordMatchOptions(
            match_mode=match_mode,
            use_regex=use_regex,
            case_sensitive=case_sensitive,
            normalize_chinese=normalize_chinese_text,
        ),
    )
    return matcher.match(text)


def _compile_regex(pattern: str, flags: int) -> re.Pattern[str]:
    try:
        return re.compile(pattern, flags=flags)
    except re.error as exc:
        raise ValueError(f"Invalid keyword regex {pattern!r}: {exc}") from exc


def _normalize(value: str, enabled: bool) -> str:
    return normalize_chinese(value) if enabled else value
