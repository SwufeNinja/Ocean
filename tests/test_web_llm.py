from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest.mock import patch

from fastapi import HTTPException

from ocean.llm.client import OpenAICompatibleClient
from ocean.models import OcrDocument, OcrPage
from ocean.web import make_app


class WebLlmConversationTest(unittest.TestCase):
    def test_llm_routes_are_registered(self) -> None:
        app = make_app(config={}, output_dir="outputs_test_web")
        routes = {route.path for route in app.routes}

        self.assertIn("/api/llm/status", routes)
        self.assertIn("/api/llm/conversations", routes)
        self.assertIn("/api/llm/conversations/{conversation_id}", routes)
        self.assertIn("/api/llm/conversations/{conversation_id}/messages", routes)
        self.assertIn("/api/llm/conversations/{conversation_id}/messages/stream", routes)

    def test_llm_conversation_keeps_multi_turn_history(self) -> None:
        app = make_app(
            config={
                "llm": {
                    "provider": "openai_compatible",
                    "api_base_url": "https://llm.example/v1",
                    "api_key": "test-key",
                    "model": "test-model",
                }
            },
            output_dir="outputs_test_web",
        )
        captured: list[tuple[list[dict[str, str]], dict[str, Any] | None]] = []
        login = _route_endpoint(app, "/api/auth/login", "POST")
        create_conversation = _route_endpoint(app, "/api/llm/conversations", "POST")
        list_conversations = _route_endpoint(app, "/api/llm/conversations", "GET")
        get_conversation = _route_endpoint(app, "/api/llm/conversations/{conversation_id}", "GET")
        send_message = _route_endpoint(app, "/api/llm/conversations/{conversation_id}/messages", "POST")
        token = login({"username": "local", "password": "local"})["access_token"]
        auth = f"Bearer {token}"

        def fake_chat(
            self: OpenAICompatibleClient,
            messages: list[dict[str, str]],
            options: dict[str, Any] | None = None,
        ) -> str:
            captured.append((messages, options))
            return f"reply {len(messages)}"

        with patch.object(OpenAICompatibleClient, "chat", fake_chat):
            create_response = create_conversation({"system_prompt": "Answer in Chinese"}, auth)
            conversation_id = create_response["conversation_id"]

            first_response = send_message(
                conversation_id,
                {"content": "hello", "options": {"temperature": 0.1}},
                auth,
            )

            second_response = send_message(
                conversation_id,
                {"content": "continue"},
                auth,
            )

        self.assertEqual(
            captured[0],
            (
                [
                    {"role": "system", "content": "Answer in Chinese"},
                    {"role": "user", "content": "hello"},
                ],
                {"temperature": 0.1},
            ),
        )
        self.assertEqual(
            captured[1][0],
            [
                {"role": "system", "content": "Answer in Chinese"},
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "reply 2"},
                {"role": "user", "content": "continue"},
            ],
        )
        self.assertIn("assistant_message", first_response)
        conversation = second_response["conversation"]
        self.assertEqual(conversation["message_count"], 4)
        self.assertEqual(conversation["title"], "hello")
        list_response = list_conversations(authorization=auth)
        self.assertEqual(list_response["count"], 1)
        self.assertEqual(list_response["conversations"][0]["conversation_id"], conversation_id)
        saved_conversation = get_conversation(conversation_id, auth)
        self.assertEqual(saved_conversation["message_count"], 4)
        self.assertEqual(
            [message["role"] for message in saved_conversation["messages"]],
            ["user", "assistant", "user", "assistant"],
        )

    def test_llm_conversation_adds_configured_web_search_tool(self) -> None:
        app = make_app(
            config={
                "llm": {
                    "provider": "openai_compatible",
                    "api_base_url": "https://llm.example/v1",
                    "api_key": "test-key",
                    "model": "test-model",
                    "web_search": {
                        "enabled": True,
                        "type": "web_search",
                        "max_keyword": 3,
                        "force_search": False,
                        "limit": 3,
                    },
                }
            },
            output_dir="outputs_test_web",
        )
        captured: list[dict[str, Any] | None] = []
        login = _route_endpoint(app, "/api/auth/login", "POST")
        create_conversation = _route_endpoint(app, "/api/llm/conversations", "POST")
        send_message = _route_endpoint(app, "/api/llm/conversations/{conversation_id}/messages", "POST")
        token = login({"username": "local", "password": "local"})["access_token"]
        auth = f"Bearer {token}"

        def fake_chat(
            self: OpenAICompatibleClient,
            messages: list[dict[str, str]],
            options: dict[str, Any] | None = None,
        ) -> str:
            captured.append(options)
            return "reply"

        with patch.object(OpenAICompatibleClient, "chat", fake_chat):
            conversation_id = create_conversation({}, auth)["conversation_id"]
            send_message(conversation_id, {"content": "today news", "options": {"temperature": 0.2}}, auth)

        self.assertEqual(
            captured[0],
            {
                "temperature": 0.2,
                "tools": [
                    {
                        "type": "web_search",
                        "max_keyword": 3,
                        "force_search": False,
                        "limit": 3,
                    }
                ],
                "tool_choice": "auto",
            },
        )

    def test_llm_conversation_can_disable_configured_web_search_per_request(self) -> None:
        app = make_app(
            config={
                "llm": {
                    "provider": "openai_compatible",
                    "api_base_url": "https://llm.example/v1",
                    "api_key": "test-key",
                    "model": "test-model",
                    "web_search": {"enabled": True, "type": "web_search"},
                }
            },
            output_dir="outputs_test_web",
        )
        captured: list[dict[str, Any] | None] = []
        login = _route_endpoint(app, "/api/auth/login", "POST")
        create_conversation = _route_endpoint(app, "/api/llm/conversations", "POST")
        send_message = _route_endpoint(app, "/api/llm/conversations/{conversation_id}/messages", "POST")
        token = login({"username": "local", "password": "local"})["access_token"]
        auth = f"Bearer {token}"

        def fake_chat(
            self: OpenAICompatibleClient,
            messages: list[dict[str, str]],
            options: dict[str, Any] | None = None,
        ) -> str:
            captured.append(options)
            return "reply"

        with patch.object(OpenAICompatibleClient, "chat", fake_chat):
            conversation_id = create_conversation({}, auth)["conversation_id"]
            send_message(conversation_id, {"content": "no search", "options": {"web_search_enabled": False}}, auth)

        self.assertIsNone(captured[0])

    def test_stream_llm_message_returns_deltas_and_saves_conversation(self) -> None:
        app = make_app(
            config={
                "llm": {
                    "provider": "openai_compatible",
                    "api_base_url": "https://llm.example/v1",
                    "api_key": "test-key",
                    "model": "test-model",
                }
            },
            output_dir="outputs_test_web",
        )
        captured: list[list[dict[str, str]]] = []

        def fake_stream_chat(
            self: OpenAICompatibleClient,
            messages: list[dict[str, str]],
            options: dict[str, Any] | None = None,
        ):
            captured.append(messages)
            yield "hel"
            yield "lo"

        login = _route_endpoint(app, "/api/auth/login", "POST")
        create_conversation = _route_endpoint(app, "/api/llm/conversations", "POST")
        stream_message = _route_endpoint(app, "/api/llm/conversations/{conversation_id}/messages/stream", "POST")
        get_conversation = _route_endpoint(app, "/api/llm/conversations/{conversation_id}", "GET")
        token = login({"username": "local", "password": "local"})["access_token"]
        auth = f"Bearer {token}"
        conversation_id = create_conversation({"system_prompt": "Answer shortly"}, auth)["conversation_id"]

        with patch.object(OpenAICompatibleClient, "stream_chat", fake_stream_chat):
            response = stream_message(conversation_id, {"content": "who are you"}, auth)
            body = asyncio.run(_read_streaming_response(response))

        self.assertIn('event: delta\ndata: {"delta": "hel"}', body)
        self.assertIn('event: delta\ndata: {"delta": "lo"}', body)
        self.assertIn("event: done", body)
        self.assertEqual(captured[0][-1], {"role": "user", "content": "who are you"})
        saved = get_conversation(conversation_id, auth)
        self.assertEqual(saved["title"], "who are you")
        self.assertEqual(saved["message_count"], 2)
        self.assertEqual(saved["messages"][1]["content"], "hello")

    def test_llm_conversation_can_bind_one_to_five_context_documents(self) -> None:
        app, auth = _authenticated_llm_app(document_store=FakeLlmDocumentStore())
        create_conversation = _route_endpoint(app, "/api/llm/conversations", "POST")

        for count in range(1, 6):
            document_ids = FakeLlmDocumentStore.document_ids[:count]
            response = create_conversation(
                {
                    "title": f"context {count}",
                    "context_documents": [
                        {"document_id": document_id}
                        for document_id in document_ids
                    ],
                },
                auth,
            )

            self.assertEqual(response["context_mode"], "documents")
            self.assertEqual(response["context_document_ids"], document_ids)
            self.assertEqual(
                [document["document_id"] for document in response["context_documents"]],
                document_ids,
            )
            self.assertEqual(
                [document["order"] for document in response["context_documents"]],
                list(range(count)),
            )

    def test_global_conversation_can_update_context_documents_when_sending(self) -> None:
        app, auth = _authenticated_llm_app(document_store=FakeLlmDocumentStore())
        create_conversation = _route_endpoint(app, "/api/llm/conversations", "POST")
        send_message = _route_endpoint(app, "/api/llm/conversations/{conversation_id}/messages", "POST")
        captured_messages: list[list[dict[str, str]]] = []

        def fake_chat(
            self: OpenAICompatibleClient,
            messages: list[dict[str, str]],
            options: dict[str, Any] | None = None,
        ) -> str:
            captured_messages.append(messages)
            return "ok"

        conversation = create_conversation({"origin": "global"}, auth)
        with patch.object(OpenAICompatibleClient, "chat", fake_chat):
            response = send_message(
                conversation["conversation_id"],
                {
                    "content": "use docs",
                    "context_documents": [{"document_id": "A"}, {"document_id": "B"}],
                },
                auth,
            )

        self.assertEqual(response["conversation"]["origin"], "global")
        self.assertEqual(response["conversation"]["context_document_ids"], ["A", "B"])
        self.assertIn("Alpha page 1 policy context.", captured_messages[0][0]["content"])
        self.assertIn("Beta page 1 background.", captured_messages[0][0]["content"])

    def test_reader_conversation_rejects_context_document_update(self) -> None:
        app, auth = _authenticated_llm_app(document_store=FakeLlmDocumentStore())
        create_conversation = _route_endpoint(app, "/api/llm/conversations", "POST")
        send_message = _route_endpoint(app, "/api/llm/conversations/{conversation_id}/messages", "POST")

        conversation = create_conversation(
            {"origin": "reader", "context_documents": [{"document_id": "A"}]},
            auth,
        )

        with self.assertRaises(HTTPException) as error:
            send_message(
                conversation["conversation_id"],
                {"content": "use another doc", "context_documents": [{"document_id": "B"}]},
                auth,
            )

        self.assertEqual(error.exception.status_code, 400)

    def test_llm_conversation_rejects_more_than_five_context_documents(self) -> None:
        app, auth = _authenticated_llm_app(document_store=FakeLlmDocumentStore())
        create_conversation = _route_endpoint(app, "/api/llm/conversations", "POST")

        with self.assertRaises(HTTPException) as too_many:
            create_conversation(
                {
                    "context_documents": [
                        {"document_id": document_id}
                        for document_id in FakeLlmDocumentStore.document_ids[:6]
                    ],
                },
                auth,
            )

        self.assertEqual(too_many.exception.status_code, 400)

    def test_llm_conversation_rejects_duplicate_context_document_ids(self) -> None:
        app, auth = _authenticated_llm_app(document_store=FakeLlmDocumentStore())
        create_conversation = _route_endpoint(app, "/api/llm/conversations", "POST")

        with self.assertRaises(HTTPException) as duplicate_document:
            create_conversation(
                {
                    "context_documents": [
                        {"document_id": "A"},
                        {"document_id": "A"},
                    ],
                },
                auth,
            )

        self.assertEqual(duplicate_document.exception.status_code, 400)

    def test_llm_conversation_document_filter_matches_context_membership(self) -> None:
        app, auth = _authenticated_llm_app(document_store=FakeLlmDocumentStore())
        create_conversation = _route_endpoint(app, "/api/llm/conversations", "POST")
        list_conversations = _route_endpoint(app, "/api/llm/conversations", "GET")
        general_id = create_conversation({"title": "general"}, auth)["conversation_id"]
        a_id = create_conversation(
            {
                "title": "A only",
                "context_documents": [{"document_id": "A"}],
            },
            auth,
        )["conversation_id"]
        ab_id = create_conversation(
            {
                "title": "A and B",
                "context_documents": [
                    {"document_id": "A"},
                    {"document_id": "B"},
                ],
            },
            auth,
        )["conversation_id"]
        b_id = create_conversation(
            {
                "title": "B only",
                "context_documents": [{"document_id": "B"}],
            },
            auth,
        )["conversation_id"]

        a_response = list_conversations(document_id="A", authorization=auth)
        b_response = list_conversations(document_id="B", authorization=auth)

        a_ids = _conversation_ids(a_response)
        b_ids = _conversation_ids(b_response)
        self.assertCountEqual(a_ids, [a_id, ab_id])
        self.assertCountEqual(b_ids, [ab_id, b_id])
        self.assertNotIn(general_id, a_ids)
        self.assertNotIn(general_id, b_ids)
        self.assertIn(ab_id, a_ids)
        self.assertIn(ab_id, b_ids)

    def test_send_message_passes_document_context_and_history_to_llm_client(self) -> None:
        app, auth = _authenticated_llm_app(document_store=FakeLlmDocumentStore())
        create_conversation = _route_endpoint(app, "/api/llm/conversations", "POST")
        send_message = _route_endpoint(app, "/api/llm/conversations/{conversation_id}/messages", "POST")
        captured: list[tuple[list[dict[str, str]], dict[str, Any] | None]] = []

        def fake_chat(
            self: OpenAICompatibleClient,
            messages: list[dict[str, str]],
            options: dict[str, Any] | None = None,
        ) -> str:
            captured.append((messages, options))
            return "first answer" if len(captured) == 1 else "second answer"

        with patch.object(OpenAICompatibleClient, "chat", fake_chat):
            conversation_id = create_conversation(
                {
                    "system_prompt": "Answer from the provided documents.",
                    "context_documents": [
                        {"document_id": "A"},
                        {"document_id": "B"},
                    ],
                },
                auth,
            )["conversation_id"]
            send_message(conversation_id, {"content": "summarize alpha"}, auth)
            send_message(conversation_id, {"content": "compare with beta"}, auth)

        second_messages = captured[1][0]
        combined_content = "\n".join(message["content"] for message in second_messages)
        self.assertIn("Answer from the provided documents.", combined_content)
        self.assertIn("document_id: A", combined_content)
        self.assertIn("a.pdf", combined_content)
        self.assertIn("Alpha page 1 policy context.", combined_content)
        self.assertIn("document_id: B", combined_content)
        self.assertIn("b.pdf", combined_content)
        self.assertIn("Beta page 2 implementation notes.", combined_content)
        self.assertIn("summarize alpha", combined_content)
        self.assertIn("first answer", combined_content)
        self.assertEqual(second_messages[-1], {"role": "user", "content": "compare with beta"})

    def test_llm_status_reports_unconfigured(self) -> None:
        app = make_app(config={}, output_dir="outputs_test_web")
        get_status = _route_endpoint(app, "/api/llm/status", "GET")

        status_response = get_status()

        self.assertFalse(status_response["configured"])

    def test_llm_validation_errors(self) -> None:
        app = make_app(config={}, output_dir="outputs_test_web")
        login = _route_endpoint(app, "/api/auth/login", "POST")
        create_conversation = _route_endpoint(app, "/api/llm/conversations", "POST")
        send_message = _route_endpoint(app, "/api/llm/conversations/{conversation_id}/messages", "POST")
        get_conversation = _route_endpoint(app, "/api/llm/conversations/{conversation_id}", "GET")
        token = login({"username": "local", "password": "local"})["access_token"]
        auth = f"Bearer {token}"

        create_response = create_conversation({}, auth)
        conversation_id = create_response["conversation_id"]

        with self.assertRaises(HTTPException) as empty_message:
            send_message(conversation_id, {"content": "   "}, auth)
        self.assertEqual(empty_message.exception.status_code, 400)

        with self.assertRaises(HTTPException) as missing_config:
            send_message(conversation_id, {"content": "hello"}, auth)
        self.assertEqual(missing_config.exception.status_code, 503)

        with self.assertRaises(HTTPException) as missing_conversation:
            get_conversation("missing", auth)
        self.assertEqual(missing_conversation.exception.status_code, 404)


class FakeLlmDocumentStore:
    document_ids = ["A", "B", "C", "D", "E", "F"]

    def __init__(self) -> None:
        self._documents: dict[str, dict[str, Any]] = {}
        self._ocr_documents: dict[str, OcrDocument] = {}
        page_sets = {
            "A": ("a.pdf", ["Alpha page 1 policy context.", "Alpha page 2 budget detail."]),
            "B": ("b.pdf", ["Beta page 1 background.", "Beta page 2 implementation notes."]),
            "C": ("c.pdf", ["Gamma page 1 context."]),
            "D": ("d.pdf", ["Delta page 1 context."]),
            "E": ("e.pdf", ["Epsilon page 1 context."]),
            "F": ("f.pdf", ["Zeta page 1 context."]),
        }
        for document_id, (file_name, pages) in page_sets.items():
            ocr_document = OcrDocument(
                source_file=file_name,
                source_path=f"/fake/{file_name}",
                ocr_engine="mineru",
                pages=[
                    OcrPage(page_number=index, text=text)
                    for index, text in enumerate(pages, start=1)
                ],
            )
            markdown = "\n\n".join(
                f"Page {page.page_number}:\n{page.text}"
                for page in ocr_document.pages
            )
            self._ocr_documents[document_id] = ocr_document
            self._documents[document_id] = {
                "account_id": "local",
                "knowledge_base_id": "default",
                "document_id": document_id,
                "file_name": file_name,
                "title": file_name,
                "status": "done",
                "ocr_engine": "mineru",
                "page_count": len(ocr_document.pages),
                "markdown": markdown,
                "ocr_json": ocr_document.to_dict(),
                "created_at": "2026-06-18T00:00:00",
                "updated_at": "2026-06-18T00:00:00",
            }

    def get_document(
        self,
        *,
        account_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> dict[str, Any] | None:
        record = self._documents.get(document_id)
        if record is None or record["knowledge_base_id"] != knowledge_base_id:
            return None
        if record["account_id"] != account_id:
            return None
        return dict(record)

    def get_markdown(
        self,
        *,
        account_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> str | None:
        record = self.get_document(
            account_id=account_id,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
        )
        return str(record.get("markdown")) if record else None

    def get_pages(
        self,
        *,
        account_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> list[dict[str, Any]]:
        document = self.load_ocr_document(
            account_id=account_id,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
        )
        if document is None:
            return []
        return [
            {
                "page_number": page.page_number,
                "text": page.text,
                "markdown": page.text,
                "blocks": [],
            }
            for page in document.pages
        ]

    def load_ocr_document(
        self,
        *,
        account_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> OcrDocument | None:
        if self.get_document(
            account_id=account_id,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
        ) is None:
            return None
        return self._ocr_documents[document_id]


def _authenticated_llm_app(
    *,
    document_store: FakeLlmDocumentStore | None = None,
) -> tuple[Any, str]:
    config: dict[str, Any] = {
        "llm": {
            "provider": "openai_compatible",
            "api_base_url": "https://llm.example/v1",
            "api_key": "test-key",
            "model": "test-model",
        }
    }
    if document_store is None:
        app = make_app(config=config, output_dir="outputs_test_web")
    else:
        config["elasticsearch"] = {"enabled": True}
        with patch("ocean.web.create_document_store", return_value=document_store):
            app = make_app(config=config, output_dir="outputs_test_web")
    login = _route_endpoint(app, "/api/auth/login", "POST")
    token = login({"username": "local", "password": "local"})["access_token"]
    return app, f"Bearer {token}"


def _conversation_ids(response: dict[str, Any]) -> list[str]:
    return [
        str(conversation["conversation_id"])
        for conversation in response["conversations"]
    ]


def _route_endpoint(app: Any, path: str, method: str) -> Any:
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"Route not found: {method} {path}")


async def _read_streaming_response(response: Any) -> str:
    chunks: list[str] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk))
    return "".join(chunks)


if __name__ == "__main__":
    unittest.main()
