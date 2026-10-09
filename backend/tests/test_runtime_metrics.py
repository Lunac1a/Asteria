"""Synthetic accounting/privacy checks; not real model measurements."""

import json
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import test_dashboard
from app.main import create_app
from app.core.config import settings
from app.services.runtime_metrics import provider_call, provider_phase
from app.services.generation_protocol import GenerationText, GenerationError, failure


class RuntimeMetricsTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(settings, "RUNTIME_METRICS_ENABLED", True))
        self.events = []
        self.enterContext(
            patch(
                "app.services.runtime_metrics.write_event",
                side_effect=lambda e: self.events.append(e),
            )
        )
        self.app = create_app()
        self.client = self.enterContext(TestClient(self.app))

    def test_thread_context_phases_usage_and_no_content(self):
        @provider_call
        def call():
            return GenerationText(
                "PRIVATE ANSWER",
                {
                    "total_tokens": 12,
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "secret": "API KEY",
                },
            )

        @self.app.post("/api/chat-fixture/{private_id}")
        def endpoint(private_id: str):
            with provider_phase("routing"):
                call()
            with provider_phase("citation_audit"):
                call()
            return {"private": "PRIVATE ANSWER"}

        result = self.client.post(
            "/api/chat-fixture/PRIVATE-ID?question=SECRET",
            json={"message": "SENSITIVE"},
            headers={"X-Asteria-Trace": "API KEY"},
        )
        event = self.events[-1]
        self.assertEqual(result.status_code, 200)
        self.assertEqual(event["total_tokens"], 24)
        self.assertEqual(
            [x["phase"] for x in event["provider_calls"]], ["routing", "citation_audit"]
        )
        self.assertTrue(event["usage_complete"])
        self.assertTrue(event["response_completed"])
        text = json.dumps(event)
        for value in ["PRIVATE", "SECRET", "SENSITIVE", "API KEY"]:
            self.assertNotIn(value, text)
        self.assertEqual(result.headers["x-asteria-trace"], event["trace_id"])

    def test_unknown_usage_and_timeout_are_not_zero_or_insufficiency(self):
        @provider_call
        def call():
            raise GenerationError("provider_timeout", "PRIVATE ERROR")

        @self.app.post("/api/timeout-fixture")
        def endpoint():
            try:
                call()
            except GenerationError as error:
                raise failure(error)

        self.assertEqual(self.client.post("/api/timeout-fixture").status_code, 504)
        event = self.events[-1]
        self.assertEqual(event["error_type"], "provider_timeout")
        self.assertEqual(event["provider_call_attempts"], 1)
        self.assertIsNone(event["total_tokens"])
        self.assertFalse(event["usage_complete"])

    def test_protocol_error_after_successful_call_retains_cost(self):
        @provider_call
        def call():
            return GenerationText("INVALID JSON", {"total_tokens": 8})

        @self.app.post("/api/invalid-fixture")
        def endpoint():
            call()
            raise failure(GenerationError("json_parse"))

        self.assertEqual(self.client.post("/api/invalid-fixture").status_code, 502)
        self.assertEqual(self.events[-1]["error_type"], "json_parse")
        self.assertEqual(self.events[-1]["total_tokens"], 8)

    def test_no_call_is_zero_and_next_request_is_isolated(self):
        self.client.get("/api/health")
        self.client.get("/api/health")
        self.assertEqual(len(self.events), 2)
        self.assertEqual(self.events[-1]["total_tokens"], 0)
        self.assertNotEqual(self.events[0]["trace_id"], self.events[1]["trace_id"])

    def test_disabled_has_no_events(self):
        with patch.object(settings, "RUNTIME_METRICS_ENABLED", False):
            self.client.get("/api/health")
        self.assertEqual(self.events, [])


class ClientTimingTests(unittest.TestCase):
    def setUp(self):
        test_dashboard.DashboardTests.setUp(self)

    def test_receiver_requires_auth_and_rejects_content_and_nan(self):
        body = {
            "trace_id": "aa54ebac-67fb-4a81-89e9-d6bb19ea8224",
            "elapsed_ms": 12.5,
            "status": 200,
            "outcome": "ok",
        }
        self.assertEqual(
            self.client.post("/api/runtime-metrics/client", json=body).status_code, 401
        )
        with patch("app.api.routes.runtime_metrics.write_event") as write:
            self.assertEqual(
                self.client.post(
                    "/api/runtime-metrics/client", headers=self.users[0][1], json=body
                ).status_code,
                204,
            )
            self.assertNotIn("user_id", write.call_args.args[0])
            self.assertEqual(
                self.client.post(
                    "/api/runtime-metrics/client",
                    headers=self.users[0][1],
                    json={**body, "message": "PRIVATE"},
                ).status_code,
                422,
            )
            self.assertEqual(
                self.client.post(
                    "/api/runtime-metrics/client",
                    headers=self.users[0][1],
                    json={**body, "elapsed_ms": -1},
                ).status_code,
                422,
            )
