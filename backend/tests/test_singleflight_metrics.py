"""Deterministic observability contracts; synthetic HTTP, no external services."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import json
import threading
import tempfile
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import Mock, patch

import test_auth  # noqa: F401
import test_dashboard
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.api.routes import chat
from app.core.config import settings
from app.core.deps import get_current_user_id
from app.db.session import get_db
from app.main import create_app
from app.schemas.chat import ChatRequest
from app.services import runtime_metrics as metrics
from app.services.chat.singleflight import Registry, fingerprint
from app.services.llm.protocol import GenerationError, GenerationText, failure


class SingleflightHTTPMetricsTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.enterContext(patch.object(settings, "RUNTIME_METRICS_ENABLED", True))
        self.enterContext(patch.object(metrics, "write_event", side_effect=self.events.append))
        self.registry = Registry()
        self.enterContext(patch.object(chat, "registry", self.registry))
        self.enterContext(patch.object(chat, "chat_permit", return_value=nullcontext()))
        self.db = Mock()
        self.db.scalar.return_value = None  # No pending deletion recovery.
        self.app = create_app()
        self.app.dependency_overrides[get_db] = lambda: self.db
        self.app.dependency_overrides[get_current_user_id] = lambda: "synthetic-user"
        self.client = self.enterContext(TestClient(self.app))
        self.body = {
            "request_id": str(uuid.uuid4()), "workspace_id": "workspace",
            "message": "PRIVATE PROMPT",
        }
        self.result = {"answer": "PRIVATE ANSWER", "session_id": "session"}
        self.trace = str(uuid.uuid4())

    def post(self, **changes):
        return self.client.post(
            "/api/chat", json={**self.body, **changes},
            headers={"X-Asteria-Trace": self.trace, "Authorization": "Bearer PRIVATE JWT"},
        )

    def pair(self, cause=None, local=False):
        entered, joined = threading.Event(), threading.Event()

        @metrics.provider_call
        def provider():
            entered.set()
            if not joined.wait(5):
                raise AssertionError("Follower never joined")
            if cause:
                raise cause
            return GenerationText("PRIVATE ANSWER", {"total_tokens": 7, "key": "PRIVATE API KEY"})

        def execute(*args):
            try:
                provider()
            except GenerationError as exc:
                raise failure(exc)
            return self.result

        original_wait = self.registry.wait

        def wait(follower, timeout):
            joined.set()
            return original_wait(follower, timeout)

        with patch.object(chat, "execute_chat", side_effect=execute), \
                patch.object(chat, "persisted_response", return_value=None if local else self.result), \
                patch.object(self.registry, "wait", side_effect=wait):
            with ThreadPoolExecutor(max_workers=2) as pool:
                leader = pool.submit(self.post)
                try:
                    if not entered.wait(5):
                        response = leader.result(1)
                        self.fail(f"Leader never entered: {response.status_code} {response.text}")
                    follower = pool.submit(self.post)
                    b = follower.result(8)
                    a = leader.result(8)
                finally:
                    joined.set()
        self.assertEqual(self.registry.running_count(), 0)
        self.assertEqual(self.registry.follower_count(), 0)
        return a, b, {
            event["singleflight_role"]: event for event in self.events
        }

    def test_joined_http_identity_cost_and_privacy(self):
        a, b, events = self.pair()
        self.assertEqual((a.status_code, b.status_code), (200, 200))
        leader, follower = events["leader"], events["follower"]
        self.assertNotEqual(leader["http_request_id"], follower["http_request_id"])
        for event in events.values():
            uuid.UUID(event["http_request_id"])
            uuid.UUID(event["flight_id"])
            self.assertEqual(event["trace_id"], self.trace)
            self.assertEqual(event["request_id"], self.body["request_id"])
        self.assertEqual(a.headers["x-asteria-trace"], self.trace)
        self.assertEqual(b.headers["x-asteria-trace"], self.trace)
        self.assertEqual(leader["flight_id"], follower["flight_id"])
        self.assertEqual(leader["provider_call_attempts"], 1)
        self.assertEqual(follower["provider_call_attempts"], 0)
        self.assertEqual(follower["total_tokens"], 0)
        self.assertEqual(follower["execution_source"], "shared_result")
        self.assertGreaterEqual(follower["follower_wait_ms"], 0)
        self.assertNotIn("follower_wait_ms", leader)
        text = json.dumps(self.events)
        for secret in ("PRIVATE", "fingerprint", "synthetic-user", "workspace"):
            self.assertNotIn(secret, text)

    def test_propagated_provider_failure_keeps_category_not_cost(self):
        a, b, events = self.pair(GenerationError("provider_timeout", "PRIVATE API KEY"))
        self.assertEqual((a.status_code, b.status_code), (504, 504))
        self.assertEqual(a.json(), b.json())
        leader, follower = events["leader"], events["follower"]
        self.assertEqual(follower["failure_origin"], "shared_execution")
        self.assertEqual(follower["error_type"], "provider_timeout")
        self.assertEqual(leader["error_type"], "provider_timeout")
        self.assertEqual(follower["provider_call_attempts"], 0)
        self.assertEqual(leader["provider_call_attempts"], 1)
        self.assertNotIn("PRIVATE", json.dumps(self.events))

    def test_follower_result_failure_is_local(self):
        a, b, events = self.pair(local=True)
        self.assertEqual((a.status_code, b.status_code), (200, 503))
        self.assertEqual(events["follower"]["failure_origin"], "follower_local")
        self.assertIsNone(events["leader"]["error_type"])
        self.assertNotIn("execution_source", events["follower"])

    def active_flight(self):
        payload = ChatRequest(**self.body)
        flight, _ = self.registry.register(
            str(payload.request_id), fingerprint(payload, "synthetic-user")
        )
        self.addCleanup(self.registry.complete, str(payload.request_id), flight)
        return flight

    def test_timeout_does_not_complete_or_fail_leader(self):
        flight = self.active_flight()
        # Deterministically exercise Event.wait's deadline result, no sleep.
        with patch.object(flight.done, "wait", return_value=False):
            response = self.post()
        self.assertEqual(response.status_code, 504)
        event = self.events[-1]
        self.assertEqual(event["failure_origin"], "follower_wait_timeout")
        self.assertEqual(event["flight_id"], flight.flight_id)
        self.assertEqual(event["provider_call_attempts"], 0)
        self.assertIn("follower_wait_ms", event)
        self.assertFalse(flight.done.is_set())
        self.assertIsNone(flight.failure)
        self.assertEqual(self.registry.running_count(), 1)
        self.assertEqual(self.registry.follower_count(), 0)

    def test_rejections_have_no_membership_or_wait(self):
        self.active_flight()
        self.registry._per_request_limit = 0
        for changes, status in (({}, 429), ({"message": "different"}, 409)):
            self.assertEqual(self.post(**changes).status_code, status)
            event = self.events[-1]
            self.assertEqual(event["singleflight_role"], "rejected")
            self.assertEqual(event["failure_origin"], "registration_rejected")
            self.assertNotIn("flight_id", event)
            self.assertNotIn("follower_wait_ms", event)
        self.assertEqual(self.registry.follower_count(), 0)

    def test_rollback_failure_is_local_and_not_waiting(self):
        self.active_flight()
        self.db.rollback.side_effect = HTTPException(503, "Synthetic rollback failure")
        self.assertEqual(self.post().status_code, 503)
        self.assertEqual(self.events[-1]["failure_origin"], "follower_local")
        self.assertNotIn("follower_wait_ms", self.events[-1])
        self.assertEqual(self.registry.follower_count(), 0)

    def test_cleanup_failure_overrides_shared_failure_origin(self):
        flight = self.active_flight()
        original_release = self.registry.release

        def wait(follower, timeout):
            # Preserve the original business exception behavior while selecting
            # the controlled propagation branch.
            follower.wait_failure_origin = "shared_execution"
            raise HTTPException(502, "Shared failure")

        def release(follower):
            original_release(follower)
            raise HTTPException(503, "Local cleanup failure")

        with patch.object(self.registry, "wait", side_effect=wait), \
                patch.object(self.registry, "release", side_effect=release):
            self.assertEqual(self.post().status_code, 503)
        self.assertEqual(self.events[-1]["failure_origin"], "follower_local")
        self.assertIn("follower_wait_ms", self.events[-1])
        self.assertEqual(flight.followers, 0)

    def test_disabled_preserves_shared_failure_and_header_contract(self):
        with patch.object(settings, "RUNTIME_METRICS_ENABLED", False):
            a, b, events = self.pair(GenerationError("provider_timeout"))
        self.assertEqual((a.status_code, b.status_code), (504, 504))
        self.assertEqual(a.json(), b.json())
        self.assertNotIn("x-asteria-trace", a.headers)
        self.assertEqual(events, {})


class ObservationBoundaryTests(unittest.TestCase):
    def test_disk_failure_does_not_fail_request(self):
        from app.services import telemetry_writer as delivery
        writer = delivery.TelemetryWriter()
        app = create_app()
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(settings, "LOCAL_DATA_DIR", directory), \
                patch.object(settings, "RUNTIME_METRICS_ENABLED", True), \
                patch.object(delivery, "writer", writer), \
                patch.object(delivery, "_Handler", side_effect=OSError("PRIVATE")), \
                TestClient(app) as client:
            try:
                response = client.get("/api/health")
                self.assertTrue(writer.flush(3))
            finally:
                self.assertTrue(writer.close(3))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(writer.snapshot()["http"]["failed"], 1)
        self.assertNotIn("PRIVATE", str(writer.snapshot()))

    def test_wait_clock_success_and_failure(self):
        for error in (None, HTTPException(504, "timeout")):
            item = {}
            token = metrics.current.set(item)
            try:
                with patch.object(metrics, "time", SimpleNamespace(
                    perf_counter=Mock(side_effect=[10.0, 10.125])
                )):
                    if error:
                        with self.assertRaises(HTTPException) as caught:
                            with metrics.follower_wait():
                                raise error
                        self.assertIs(caught.exception, error)
                    else:
                        with metrics.follower_wait():
                            pass
                self.assertEqual(item["follower_wait_ms"], 125.0)
            finally:
                metrics.current.reset(token)

    def test_broken_observation_cannot_replace_business_exception(self):
        error = HTTPException(409, "business")
        token = metrics.current.set({})
        try:
            with patch.object(metrics, "time", SimpleNamespace(
                perf_counter=Mock(side_effect=OSError("clock unavailable"))
            )):
                with self.assertRaises(HTTPException) as caught:
                    with metrics.follower_wait():
                        raise error
                self.assertIs(caught.exception, error)
            metrics.singleflight_fields(
                request_id="PRIVATE PROMPT", fingerprint=("PRIVATE API KEY",),
            )
            self.assertEqual(metrics.current.get(), {})
            with patch.object(metrics.uuid, "uuid4", side_effect=OSError("entropy")):
                self.assertIsNone(metrics.observation_id())
        finally:
            metrics.current.reset(token)

    def test_old_follower_keeps_original_flight_and_category(self):
        registry = Registry()
        old, _ = registry.register("business", ())
        follower, _ = registry.register("business", ())
        registry.complete("business", old, failure(GenerationError("provider_connection")))
        new, _ = registry.register("business", ())
        self.assertNotEqual(old.flight_id, new.flight_id)
        with self.assertRaises(HTTPException):
            registry.wait(follower, 0)
        self.assertEqual(follower.flight.flight_id, old.flight_id)
        self.assertEqual(follower.flight.failure_category, "provider_connection")
        registry.release(follower)
        registry.complete("business", new)
        self.assertEqual(registry.follower_count(), 0)


class PersistedReplayMetricsTests(unittest.TestCase):
    def setUp(self):
        test_dashboard.DashboardTests.setUp(self)

    def test_real_route_persistence_replay_and_disabled(self):
        headers = self.users[0][1]
        workspace = self.client.post(
            "/api/workspaces", headers=headers, json={"name": "Synthetic"}
        ).json()["id"]
        body = {"workspace_id": workspace, "message": "PRIVATE PROMPT",
                "request_id": str(uuid.uuid4())}
        events = []

        @metrics.provider_call
        def answer(*args, **kwargs):
            return ("Synthetic answer", [], "general")

        with patch.object(settings, "RUNTIME_METRICS_ENABLED", True), \
                patch.object(metrics, "write_event", side_effect=events.append), \
                patch.object(chat, "decide", return_value={
                    "action": "direct", "reason": "general", "origin": "test",
                }), \
                patch.object(chat, "grounded_answer", side_effect=answer) as generate:
            first = self.client.post("/api/chat", headers=headers, json=body)
            second = self.client.post("/api/chat", headers=headers, json=body)
            with patch.object(settings, "RUNTIME_METRICS_ENABLED", False):
                third = self.client.post("/api/chat", headers=headers, json=body)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(first.json(), third.json())
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(len(events), 2)
        fresh, replay = events
        self.assertEqual(fresh["execution_source"], "fresh_generation")
        self.assertEqual(replay["execution_source"], "persisted_replay")
        self.assertEqual(replay["singleflight_role"], "leader")
        self.assertNotEqual(fresh["flight_id"], replay["flight_id"])
        self.assertEqual(fresh["provider_call_attempts"], 1)
        self.assertEqual(replay["provider_call_attempts"], 0)
        self.assertNotIn("PRIVATE", json.dumps(events))
