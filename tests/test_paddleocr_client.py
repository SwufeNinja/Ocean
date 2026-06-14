from __future__ import annotations

import unittest
from unittest.mock import patch

from ocean.ocr.paddle import PaddleOcrClient, PaddleOcrHttpError, _parse_paddle_error_body


class PaddleOcrClientRetryTest(unittest.TestCase):
    def test_retries_queue_full_with_exponential_backoff(self) -> None:
        client = PaddleOcrClient(api_base_url="https://example.test/jobs", api_key="token")
        responses = [
            PaddleOcrHttpError(400, '{"code":10010,"msg":"任务提交队列已满，请稍后重试"}', 10010, "任务提交队列已满，请稍后重试"),
            PaddleOcrHttpError(503, "busy"),
            {"data": {"jobId": "ok"}},
        ]

        def request_json(*_args, **_kwargs):
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        with (
            patch.object(client, "_request_json", side_effect=request_json),
            patch("ocean.ocr.paddle.time.sleep") as sleep,
        ):
            data = client._request_json_with_retries(
                "https://example.test/jobs",
                method="POST",
                body=b"pdf",
                headers={},
                timeout=10,
                options={
                    "retry_initial_delay_seconds": 30,
                    "retry_max_delay_seconds": 300,
                    "retry_max_wait_seconds": 1000,
                },
                operation="submit sample.pdf",
            )

        self.assertEqual(data, {"data": {"jobId": "ok"}})
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [30, 60])

    def test_does_not_retry_non_retryable_error(self) -> None:
        client = PaddleOcrClient(api_base_url="https://example.test/jobs", api_key="token")

        with (
            patch.object(
                client,
                "_request_json",
                side_effect=PaddleOcrHttpError(400, '{"code":10006,"msg":"page limit"}', 10006, "page limit"),
            ),
            patch("ocean.ocr.paddle.time.sleep") as sleep,
        ):
            with self.assertRaises(PaddleOcrHttpError):
                client._request_json_with_retries(
                    "https://example.test/jobs",
                    method="POST",
                    body=b"pdf",
                    headers={},
                    timeout=10,
                    options={"retry_max_wait_seconds": 1000},
                    operation="submit sample.pdf",
                )

        sleep.assert_not_called()

    def test_parse_paddle_error_body(self) -> None:
        self.assertEqual(_parse_paddle_error_body('{"code":10010,"msg":"queue full"}'), (10010, "queue full"))
        self.assertEqual(_parse_paddle_error_body('{"code":"10010","msg":"queue full"}'), (10010, "queue full"))
        self.assertEqual(_parse_paddle_error_body("not-json"), (None, None))


if __name__ == "__main__":
    unittest.main()
