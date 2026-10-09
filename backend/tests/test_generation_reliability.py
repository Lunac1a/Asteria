"""Fixed synthetic faults, separate from real-provider results. No network or private DB."""

import json
import subprocess
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch, Mock
from fastapi import HTTPException
import httpx
from openai import APITimeoutError
import test_auth  # noqa: F401 - initialize isolated test settings before app imports
from app.core.config import settings
from app.services.generation_protocol import (
    GenerationError,
    response_format,
    chat_schema,
)
from app.services.nvidia_nim_api_service import (
    decode_response,
    generate_response,
    request_provider,
)
from app.services.rag_service import grounded_answer
from app.services.learning_service import generate_recap

CASES = json.loads(
    (Path(__file__).resolve().parent / "fixtures/generation_cases.json").read_text()
)


class GenerationContracts(unittest.TestCase):
    def run_case(self, case):
        try:
            if "transport" in case:
                if case["transport"] == "timeout":
                    effect = subprocess.TimeoutExpired("worker", 45)
                    with patch(
                        "app.services.nvidia_nim_api_service.subprocess.run",
                        side_effect=effect,
                    ):
                        generate_response(
                            [],
                            "synthetic",
                            "https://integrate.api.nvidia.com/v1",
                            "test",
                        )
                else:
                    envelope = {
                        "error": "Safe provider error",
                        "category": case["expected"],
                    }
                    with patch(
                        "app.services.nvidia_nim_api_service.subprocess.run",
                        return_value=NS(stdout=json.dumps(envelope), returncode=0),
                    ):
                        generate_response(
                            [],
                            "synthetic",
                            "https://integrate.api.nvidia.com/v1",
                            "test",
                        )
            else:
                response = NS(
                    usage=NS(prompt_tokens=10, completion_tokens=5, total_tokens=15),
                    choices=[
                        NS(
                            finish_reason=case["finish"],
                            message=NS(
                                content=case["raw"],
                                refusal=case.get("refusal"),
                                tool_calls=None,
                                function_call=None,
                            ),
                        )
                    ],
                )
                text = decode_response(response, time.perf_counter())
                self.assertEqual(text.metadata["total_tokens"], 15)
                with (
                    patch.object(settings, "LLM_BACKEND", "provider"),
                    patch(
                        "app.services.rag_service.provider_completion",
                        return_value=text,
                    ),
                ):
                    grounded_answer(
                        None,
                        "test",
                        "Synthetic question",
                        [],
                        [{"number": 1, "content": "Synthetic evidence"}],
                        answer_mode="hybrid",
                    )
            actual = "accepted"
        except GenerationError as error:
            actual = error.category
        except HTTPException as error:
            actual = error.generation_category
        self.assertEqual(actual, case["expected"], case["id"])

    def test_capability_is_exact_pair_opt_in(self):
        profiles = [{"base_url": "https://example.invalid/v1", "model": "verified"}]
        self.assertIsNone(
            response_format(
                "https://other.invalid/v1", "verified", chat_schema(), profiles
            )
        )
        self.assertIsNone(
            response_format(
                "https://example.invalid/v1", "unverified", chat_schema(), profiles
            )
        )
        self.assertTrue(
            response_format(
                "https://example.invalid/v1/", "verified", chat_schema(), profiles
            )["json_schema"]["strict"]
        )

    def test_sdk_timeout_has_one_request(self):
        calls = []

        def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout("Synthetic", request=request)

        with patch(
            "app.services.nvidia_nim_api_service.Client",
            return_value=httpx.Client(transport=httpx.MockTransport(handler)),
        ):
            with self.assertRaises(APITimeoutError):
                request_provider([], "synthetic", "https://example.invalid/v1", "test")
        self.assertEqual(len(calls), 1)

    def test_worker_must_exit_successfully_even_with_valid_json(self):
        for output, code in [("[]", 0), ('{"answer":"valid-looking"}', 1)]:
            with (
                self.subTest(code=code),
                patch(
                    "app.services.nvidia_nim_api_service.subprocess.run",
                    return_value=NS(stdout=output, returncode=code),
                ),
            ):
                with self.assertRaises(GenerationError) as caught:
                    generate_response(
                        [], "synthetic", "https://integrate.api.nvidia.com/v1", "test"
                    )
                self.assertEqual(caught.exception.category, "provider_protocol")

    def test_sdk_serializes_schema_and_retains_usage(self):
        fmt = response_format(
            "https://example.invalid/v1",
            "verified",
            chat_schema(),
            [{"base_url": "https://example.invalid/v1", "model": "verified"}],
        )
        calls = []

        def handler(request):
            body = json.loads(request.content)
            calls.append(body)
            self.assertEqual(body["response_format"], fmt)
            self.assertEqual(body["max_tokens"], 2048)
            return httpx.Response(
                200,
                json={
                    "id": "synthetic",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "verified",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "{}"},
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                        "total_tokens": 12,
                    },
                },
            )

        with patch(
            "app.services.nvidia_nim_api_service.Client",
            return_value=httpx.Client(transport=httpx.MockTransport(handler)),
        ):
            answer = request_provider(
                [],
                "synthetic",
                "https://example.invalid/v1",
                "verified",
                response_format=fmt,
            )
        self.assertEqual(answer.metadata["total_tokens"], 12)
        self.assertEqual(answer.metadata["finish_reason"], "stop")
        self.assertEqual(len(calls), 1)

    def test_recap_field_validation_and_classification(self):
        db = Mock()
        db.query.return_value.filter_by.return_value.first.return_value = NS(
            base_url="https://integrate.api.nvidia.com/v1",
            encrypted_api_key="unused",
            model_name="test",
        )
        samples = [
            ('{"explored":"x"}', "schema_validation"),
            ('{"explored":1,"tried":"","unclear":"","next":""}', "schema_validation"),
            ('{"explored":', "json_parse"),
            (GenerationError("output_truncated"), "output_truncated"),
            (GenerationError("provider_timeout"), "provider_timeout"),
        ]
        with (
            patch.object(settings, "LLM_BACKEND", "provider"),
            patch(
                "app.services.learning_service.decrypt_text", return_value="synthetic"
            ),
        ):
            for raw, expected in samples:
                with (
                    self.subTest(expected=expected),
                    patch(
                        "app.services.learning_service.generate_response",
                        **(
                            {"side_effect": raw}
                            if isinstance(raw, Exception)
                            else {"return_value": raw}
                        ),
                    ) as call,
                ):
                    with self.assertRaises(HTTPException) as caught:
                        generate_recap(db, "test", {})
                    self.assertEqual(caught.exception.generation_category, expected)
                    self.assertEqual(call.call_count, 1)

    def test_production_logs_do_not_contain_raw_answer(self):
        from app.services.generation_protocol import failure

        with self.assertLogs(
            "app.services.generation_protocol", level="WARNING"
        ) as logs:
            failure(GenerationError("json_parse", "PRIVATE RESPONSE SENTINEL"))
        self.assertNotIn("PRIVATE RESPONSE", str(logs.output))


for case in CASES:
    setattr(
        GenerationContracts,
        "test_fixture_" + case["id"].replace("-", "_"),
        lambda self, c=case: self.run_case(c),
    )
