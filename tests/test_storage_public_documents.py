from __future__ import annotations

import unittest
from typing import Any

from ocean.storage.elasticsearch import ElasticsearchDocumentStore, PUBLIC_ACCOUNT_ID


class PublicDocumentVisibilityTest(unittest.TestCase):
    def test_document_reads_include_public_account(self) -> None:
        store, client = _store_with_fake_client()

        store.list_documents(account_id="new-user", knowledge_base_id="default")
        store.get_document(account_id="new-user", knowledge_base_id="default", document_id="guide")
        store.get_pages(account_id="new-user", knowledge_base_id="default", document_id="guide")

        account_filters = [
            _find_account_terms(call)
            for call in client.search_calls
        ]

        self.assertEqual(
            account_filters,
            [
                ["new-user", PUBLIC_ACCOUNT_ID],
                ["new-user", PUBLIC_ACCOUNT_ID],
                ["new-user", PUBLIC_ACCOUNT_ID],
            ],
        )


class FakeElasticsearchClient:
    def __init__(self) -> None:
        self.search_calls: list[dict[str, Any]] = []

    def search(self, **kwargs: Any) -> dict[str, Any]:
        self.search_calls.append(kwargs)
        return {"hits": {"hits": []}}


def _store_with_fake_client() -> tuple[ElasticsearchDocumentStore, FakeElasticsearchClient]:
    client = FakeElasticsearchClient()
    store = ElasticsearchDocumentStore.__new__(ElasticsearchDocumentStore)
    store.client = client
    store.index_prefix = "ocean"
    store.request_timeout = 30
    store.analyzer = "ik_max_word"
    store.search_analyzer = "ik_smart"
    return store, client


def _find_account_terms(call: dict[str, Any]) -> list[str]:
    query = call.get("query") or call.get("body", {}).get("query", {})
    for item in query["bool"]["filter"]:
        if "terms" in item and "account_id" in item["terms"]:
            return item["terms"]["account_id"]
    raise AssertionError(f"account_id terms filter not found: {query}")


if __name__ == "__main__":
    unittest.main()
