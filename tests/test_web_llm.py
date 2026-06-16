from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from fastapi import HTTPException

from ocean.llm.client import OpenAICompatibleClient
from ocean.web import make_app


class WebLlmConversationTest(unittest.TestCase):
    def test_llm_routes_are_registered(self) -> None:
        app = make_app(config={}, output_dir="outputs_test_web")
        routes = {route.path for route in app.routes}

        self.assertIn("/api/llm/status", routes)
        self.assertIn("/api/llm/conversations", routes)
        self.assertIn("/api/llm/conversations/{conversation_id}", routes)
        self.assertIn("/api/llm/conversations/{conversation_id}/messages", routes)

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
        create_conversation = _route_endpoint(app, "/api/llm/conversations", "POST")
        send_message = _route_endpoint(app, "/api/llm/conversations/{conversation_id}/messages", "POST")

        def fake_chat(
            self: OpenAICompatibleClient,
            messages: list[dict[str, str]],
            options: dict[str, Any] | None = None,
        ) -> str:
            captured.append((messages, options))
            return f"reply {len(messages)}"

        with patch.object(OpenAICompatibleClient, "chat", fake_chat):
            create_response = create_conversation({"system_prompt": "Answer in Chinese"})
            conversation_id = create_response["conversation_id"]

            first_response = send_message(
                conversation_id,
                {"content": "hello", "options": {"temperature": 0.1}},
            )

            second_response = send_message(
                conversation_id,
                {"content": "continue"},
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

    def test_llm_status_and_validation_errors(self) -> None:
        app = make_app(config={}, output_dir="outputs_test_web")
        get_status = _route_endpoint(app, "/api/llm/status", "GET")
        create_conversation = _route_endpoint(app, "/api/llm/conversations", "POST")
        send_message = _route_endpoint(app, "/api/llm/conversations/{conversation_id}/messages", "POST")
        get_conversation = _route_endpoint(app, "/api/llm/conversations/{conversation_id}", "GET")

        status_response = get_status()
        self.assertFalse(status_response["configured"])

        create_response = create_conversation({})
        conversation_id = create_response["conversation_id"]

        with self.assertRaises(HTTPException) as empty_message:
            send_message(conversation_id, {"content": "   "})
        self.assertEqual(empty_message.exception.status_code, 400)

        with self.assertRaises(HTTPException) as missing_config:
            send_message(conversation_id, {"content": "hello"})
        self.assertEqual(missing_config.exception.status_code, 503)

        with self.assertRaises(HTTPException) as missing_conversation:
            get_conversation("missing")
        self.assertEqual(missing_conversation.exception.status_code, 404)


def _route_endpoint(app: Any, path: str, method: str) -> Any:
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"Route not found: {method} {path}")


if __name__ == "__main__":
    unittest.main()
