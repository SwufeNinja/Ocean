from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from ocean.llm.client import OpenAICompatibleClient


class FakeResponse:
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")


class FakeStreamResponse(FakeResponse):
    def __iter__(self):
        return iter(
            [
                b'data: {"choices":[{"delta":{"content":"he"}}]}\n\n',
                b'data: {"choices":[{"delta":{"content":"llo"}}]}\n\n',
                b"data: [DONE]\n\n",
            ]
        )


class OpenAICompatibleClientTest(unittest.TestCase):
    def test_token_limit_field_can_use_max_completion_tokens(self) -> None:
        captured_payload: dict[str, object] = {}

        def fake_urlopen(request, timeout: int):
            captured_payload.update(json.loads(request.data.decode("utf-8")))
            return FakeResponse()

        client = OpenAICompatibleClient.from_config(
            {
                "api_base_url": "https://api.xiaomimimo.com/v1",
                "api_key": "test-key",
                "model": "mimo-v2.5-pro",
                "max_tokens": 123,
                "token_limit_field": "max_completion_tokens",
            }
        )

        with patch("urllib.request.urlopen", fake_urlopen):
            result = client.chat([{"role": "user", "content": "hello"}])

        self.assertEqual(result, "ok")
        self.assertEqual(captured_payload["max_completion_tokens"], 123)
        self.assertNotIn("max_tokens", captured_payload)

    def test_stream_chat_yields_openai_compatible_deltas(self) -> None:
        captured_payload: dict[str, object] = {}

        def fake_urlopen(request, timeout: int):
            captured_payload.update(json.loads(request.data.decode("utf-8")))
            return FakeStreamResponse()

        client = OpenAICompatibleClient.from_config(
            {
                "api_base_url": "https://api.xiaomimimo.com/v1",
                "api_key": "test-key",
                "model": "mimo-v2.5-pro",
            }
        )

        with patch("urllib.request.urlopen", fake_urlopen):
            result = "".join(client.stream_chat([{"role": "user", "content": "hello"}]))

        self.assertEqual(result, "hello")
        self.assertIs(captured_payload["stream"], True)


if __name__ == "__main__":
    unittest.main()
