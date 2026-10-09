"""Real SDK contract with synthetic in-memory HTTP responses; no model calls."""

import json
import subprocess
import unittest
from unittest.mock import patch
import httpx
from openai import APIStatusError, RateLimitError
from test_auth import Base  # noqa: F401 (synthetic settings before application imports)
from app.services.nvidia_nim_api_service import generate_response, request_provider


class ProviderContractTests(unittest.TestCase):
    def call(self, handler):
        client = httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=False
        )
        with patch(
            "app.services.nvidia_nim_api_service.Client", return_value=client
        ):
            return request_provider(
                [{"role": "user", "content": "Independent test"}],
                "synthetic-test-key",
                "https://integrate.api.nvidia.com/v1",
                "synthetic-model",
            )

    def test_sdk_serializes_bounded_chat_and_reads_answer(self):
        def handler(request):
            body = json.loads(request.content)
            self.assertEqual(body["max_tokens"], 2048)
            self.assertEqual(body["model"], "synthetic-model")
            return httpx.Response(
                200,
                json={
                    "id": "synthetic",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "synthetic-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "Independent answer",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                },
            )

        self.assertEqual(self.call(handler), "Independent answer")

    def test_provider_redirect_is_not_followed(self):
        calls = []

        def handler(request):
            calls.append(str(request.url))
            return httpx.Response(
                302,
                headers={"Location": "https://untrusted.invalid/collect"},
                json={"error": {"message": "redirect"}},
            )

        with self.assertRaises(APIStatusError):
            self.call(handler)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("untrusted.invalid", calls[0])

    def test_rate_limit_has_no_automatic_retry(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(
                429, json={"error": {"message": "Synthetic rate limit"}}
            )

        with self.assertRaises(RateLimitError):
            self.call(handler)
        self.assertEqual(len(calls), 1)

    def test_total_deadline_does_not_put_credentials_in_argv(self):
        with patch(
            "app.services.nvidia_nim_api_service.subprocess.run",
            side_effect=subprocess.TimeoutExpired("worker", 45),
        ) as run:
            with self.assertRaisesRegex(RuntimeError, "45-second"):
                generate_response(
                    [],
                    "synthetic-secret-never-argv",
                    "https://integrate.api.nvidia.com/v1",
                    "synthetic-model",
                )
        self.assertEqual(run.call_args.kwargs["timeout"], 45)
        self.assertNotIn("synthetic-secret-never-argv", str(run.call_args.args))
        self.assertNotIn("NVIDIA_API_KEY", run.call_args.kwargs["env"])
