from __future__ import annotations

import unittest
from copy import deepcopy
from typing import Any

from ocean.storage.elasticsearch import ElasticsearchDocumentStore


class LlmConversationStorageTest(unittest.TestCase):
    def test_ensure_indices_creates_llm_indices(self) -> None:
        store, client = _store_with_fake_client()

        store.ensure_indices()

        self.assertIn("ocean_llm_conversations_v1", client.indices.created)
        self.assertIn("ocean_llm_messages_v1", client.indices.created)
        conversation_properties = client.indices.created["ocean_llm_conversations_v1"]["mappings"]["properties"]
        message_properties = client.indices.created["ocean_llm_messages_v1"]["mappings"]["properties"]
        self.assertEqual(conversation_properties["context_document_ids"], {"type": "keyword"})
        self.assertEqual(message_properties["sequence"], {"type": "integer"})

    def test_list_conversations_filters_by_context_document_contains(self) -> None:
        store, client = _store_with_fake_client()

        saved = store.save_llm_conversation(
            {
                "conversation_id": "conv-1",
                "account_id": "acct",
                "knowledge_base_id": "kb",
                "context_documents": [{"document_id": "doc-a"}, {"document_id": "doc-b"}],
            }
        )
        listed = store.list_llm_conversations(
            {
                "account_id": "acct",
                "knowledge_base_id": "kb",
                "document_id": "doc-a",
                "context_mode": "documents",
                "limit": 1000,
            }
        )

        self.assertEqual(saved["context_document_ids"], ["doc-a", "doc-b"])
        self.assertEqual(saved["context_mode"], "documents")
        self.assertEqual([conversation["conversation_id"] for conversation in listed], ["conv-1"])
        body = client.search_calls[-1]["body"]
        self.assertEqual(body["size"], 500)
        self.assertIn({"term": {"context_document_ids": "doc-a"}}, body["query"]["bool"]["filter"])
        self.assertEqual(body["query"]["bool"]["must_not"], [{"term": {"status": "deleted"}}])

    def test_append_messages_assigns_sequence_and_lists_ascending(self) -> None:
        store, client = _store_with_fake_client()
        store.save_llm_conversation(
            {
                "conversation_id": "conv-1",
                "account_id": "acct",
                "knowledge_base_id": "kb",
                "message_count": 0,
            }
        )

        appended = store.append_llm_messages(
            {
                "conversation_id": "conv-1",
                "account_id": "acct",
                "knowledge_base_id": "kb",
                "messages": [
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": "hi"},
                ],
                "conversation_updates": {"title": "hello"},
            }
        )
        messages = store.list_llm_messages(
            {"conversation_id": "conv-1", "account_id": "acct", "knowledge_base_id": "kb"}
        )
        conversation = client.documents["ocean_llm_conversations_v1"]["conv-1"]

        self.assertEqual([message["sequence"] for message in appended], [1, 2])
        self.assertEqual([message["sequence"] for message in messages], [1, 2])
        self.assertEqual(conversation["message_count"], 2)
        self.assertEqual(conversation["title"], "hello")


class FakeIndicesClient:
    def __init__(self) -> None:
        self.created: dict[str, dict[str, Any]] = {}

    def exists(self, *, index: str) -> bool:
        return index in self.created

    def create(self, *, index: str, body: dict[str, Any]) -> None:
        self.created[index] = body


class FakeElasticsearchClient:
    def __init__(self) -> None:
        self.indices = FakeIndicesClient()
        self.documents: dict[str, dict[str, dict[str, Any]]] = {}
        self.search_calls: list[dict[str, Any]] = []

    def index(self, *, index: str, id: str, document: dict[str, Any]) -> None:
        self.documents.setdefault(index, {})[id] = deepcopy(document)

    def get(self, *, index: str, id: str) -> dict[str, Any]:
        document = self.documents.get(index, {}).get(id)
        if document is None:
            return {"found": False}
        return {"found": True, "_source": deepcopy(document)}

    def update(self, *, index: str, id: str, doc: dict[str, Any]) -> None:
        self.documents[index][id].update(deepcopy(doc))

    def search(self, **kwargs: Any) -> dict[str, Any]:
        self.search_calls.append(deepcopy(kwargs))
        index = kwargs["index"]
        body = kwargs.get("body") or {}
        query = kwargs.get("query") or body.get("query") or {}
        hits = [
            {"_source": deepcopy(document)}
            for document in self.documents.get(index, {}).values()
            if _matches_query(document, query)
        ]
        for sort_clause in reversed(body.get("sort") or kwargs.get("sort") or []):
            field, options = next(iter(sort_clause.items()))
            reverse = (options or {}).get("order") == "desc"
            hits.sort(key=lambda hit: hit["_source"].get(field) or "", reverse=reverse)
        size = body.get("size", kwargs.get("size", 10))
        return {"hits": {"hits": hits[:size]}}


def _store_with_fake_client() -> tuple[ElasticsearchDocumentStore, FakeElasticsearchClient]:
    client = FakeElasticsearchClient()
    store = ElasticsearchDocumentStore.__new__(ElasticsearchDocumentStore)
    store.client = client
    store.index_prefix = "ocean"
    store.request_timeout = 30
    store.analyzer = "ik_max_word"
    store.search_analyzer = "ik_smart"
    return store, client


def _matches_query(document: dict[str, Any], query: dict[str, Any]) -> bool:
    bool_query = query.get("bool", {})
    for clause in bool_query.get("filter", []):
        if not _matches_clause(document, clause):
            return False
    for clause in bool_query.get("must_not", []):
        if _matches_clause(document, clause):
            return False
    return True


def _matches_clause(document: dict[str, Any], clause: dict[str, Any]) -> bool:
    if "term" in clause:
        field, expected = next(iter(clause["term"].items()))
        actual = document.get(field)
        if isinstance(actual, list):
            return expected in actual
        return actual == expected
    if "terms" in clause:
        field, expected = next(iter(clause["terms"].items()))
        actual = document.get(field)
        if isinstance(actual, list):
            return any(item in expected for item in actual)
        return actual in expected
    return True


if __name__ == "__main__":
    unittest.main()
