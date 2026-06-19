from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any


class OpenAICompatibleClient:
    def __init__(
        self,
        api_base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0,
        max_tokens: int = 4096,
        token_limit_field: str = "max_tokens",
        timeout_seconds: int = 120,
        retry: int = 3,
        request_options: dict[str, Any] | None = None,
    ) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.token_limit_field = token_limit_field if token_limit_field in {"max_tokens", "max_completion_tokens"} else "max_tokens"
        self.timeout_seconds = timeout_seconds
        self.retry = retry
        self.request_options = request_options or {}

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "OpenAICompatibleClient":
        return cls(
            api_base_url=config.get("api_base_url", ""),
            api_key=config.get("api_key", ""),
            model=config.get("model", ""),
            temperature=float(config.get("temperature", 0)),
            max_tokens=int(config.get("max_tokens", 4096)),
            token_limit_field=str(config.get("token_limit_field", "max_tokens")),
            timeout_seconds=int(config.get("timeout_seconds", 120)),
            retry=int(config.get("retry", 3)),
            request_options=config.get("request_options") if isinstance(config.get("request_options"), dict) else None,
        )

    def chat(self, messages: list[dict[str, str]], options: dict[str, Any] | None = None) -> str:
        if not self.api_base_url or not self.api_key or not self.model:
            raise ValueError("LLM api_base_url, api_key, and model must be configured.")
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            self.token_limit_field: self.max_tokens,
        }
        if self.request_options:
            payload.update(self.request_options)
        if options:
            payload.update(options)

        request = urllib.request.Request(
            f"{self.api_base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        last_error: Exception | None = None
        for attempt in range(1, self.retry + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    data = json.loads(response.read().decode("utf-8"))
                return data["choices"][0]["message"]["content"]
            except (urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < self.retry:
                    time.sleep(min(2**attempt, 10))
        raise RuntimeError(f"LLM request failed after {self.retry} attempts: {last_error}")

    def stream_chat(self, messages: list[dict[str, str]], options: dict[str, Any] | None = None) -> Iterator[str]:
        if not self.api_base_url or not self.api_key or not self.model:
            raise ValueError("LLM api_base_url, api_key, and model must be configured.")
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            self.token_limit_field: self.max_tokens,
            "stream": True,
        }
        if self.request_options:
            payload.update(self.request_options)
        if options:
            payload.update(options)
        payload["stream"] = True

        request = urllib.request.Request(
            f"{self.api_base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        last_error: Exception | None = None
        for attempt in range(1, self.retry + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    for raw_line in response:
                        line = raw_line.decode("utf-8", errors="ignore").strip()
                        if not line or line.startswith(":"):
                            continue
                        if line.startswith("data:"):
                            line = line[5:].strip()
                        if line == "[DONE]":
                            return
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        for choice in data.get("choices") or []:
                            delta = choice.get("delta") or {}
                            content = delta.get("content")
                            if content:
                                yield str(content)
                            message_content = (choice.get("message") or {}).get("content")
                            if message_content:
                                yield str(message_content)
                return
            except urllib.error.URLError as exc:
                last_error = exc
                if attempt < self.retry:
                    time.sleep(min(2**attempt, 10))
        raise RuntimeError(f"LLM streaming request failed after {self.retry} attempts: {last_error}")
