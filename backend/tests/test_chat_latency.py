"""Stage contracts with synthetic clocks plus one bounded wall-clock commit delay."""
import json
import threading
import time
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import Mock, patch

import test_dashboard
from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.api.routes import chat
from app.core.config import settings
from app.models.chat_turn import ChatTurn
from app.models.messages import Message
from app.services import runtime_metrics as metrics
import test_singleflight_metrics


class Clock:
    def __init__(self):
        self.value = 100.0

    def now(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class IntervalTests(unittest.TestCase):
    def test_recording_and_coverage_failures_are_nonfatal(self):
        item = {"operation": "chat", "stages": Mock()}
        item["stages"].append.side_effect = OSError("PRIVATE")
        token = metrics.current.set(item)
        origin = metrics.request_started.set(time.perf_counter())
        try:
            with metrics.chat_stage("generation"):
                pass
            metrics.finish_stage_coverage(item)
            self.assertNotIn("unexplained_ms", item)
        finally:
            metrics.request_started.reset(origin)
            metrics.current.reset(token)

    def test_union_clipping_and_provider_not_added(self):
        item = {"operation": "chat", "server_elapsed_ms": 100,
                "provider_calls": [{"elapsed_ms": 999}],
                "stages": [{"start_ms": a, "end_ms": b}
                           for a, b in [(10, 40), (20, 50), (60, 80), (75, 120)]]}
        metrics.finish_stage_coverage(item)
        self.assertEqual(item["stage_covered_ms"], 80)
        self.assertEqual(item["unexplained_ms"], 20)

    def test_reference_outcome_privacy_and_clock_failure(self):
        clock = Clock()
        item = {"operation": "chat"}
        token = metrics.current.set(item)
        origin = metrics.request_started.set(100)
        try:
            with patch.object(metrics, "time", SimpleNamespace(perf_counter=clock.now)):
                clock.advance(.01)
                error = HTTPException(503, "PRIVATE DOCUMENT")
                with self.assertRaises(HTTPException) as caught:
                    with metrics.chat_stage("persistence"):
                        clock.advance(.3)
                        raise error
                self.assertIs(caught.exception, error)
                with metrics.chat_stage("PRIVATE JWT"):
                    pass
            self.assertEqual(item["stages"], [{
                "name": "persistence", "start_ms": 10, "end_ms": 310,
                "duration_ms": 300, "outcome": "failure",
            }])
            with patch.object(metrics, "time", SimpleNamespace(
                perf_counter=Mock(side_effect=OSError("PRIVATE API KEY"))
            )):
                with self.assertRaises(HTTPException) as caught:
                    with metrics.chat_stage("generation"):
                        raise error
                self.assertIs(caught.exception, error)
            self.assertNotIn("PRIVATE", json.dumps(item))
        finally:
            metrics.request_started.reset(origin)
            metrics.current.reset(token)


class ChatLatencyTests(unittest.TestCase):
    def setUp(self):
        test_dashboard.DashboardTests.setUp(self)
        self.headers = self.users[0][1]
        self.workspace = self.client.post(
            "/api/workspaces", headers=self.headers, json={"name": "Synthetic"}
        ).json()["id"]
        self.events = []
        self.enterContext(patch.object(settings, "RUNTIME_METRICS_ENABLED", True))
        self.enterContext(patch.object(metrics, "write_event", side_effect=self.events.append))
        self.enterContext(patch.object(chat, "decide", return_value={
            "action": "retrieve", "reason": "test", "origin": "test",
        }))
        self.enterContext(patch.object(chat, "retrieve", return_value=[]))
        self.clock = Clock()

        @metrics.provider_call
        def answer(*args, **kwargs):
            self.clock.advance(.05)
            return "PRIVATE ANSWER", [], "general"

        self.generate = self.enterContext(patch.object(chat, "grounded_answer", side_effect=answer))

    def post(self, request_id=None):
        return self.client.post("/api/chat", headers=self.headers, json={
            "workspace_id": self.workspace, "message": "PRIVATE PROMPT",
            "request_id": request_id or str(uuid.uuid4()),
        })

    def values(self, event):
        return {
            "server_ms": event["server_elapsed_ms"],
            "generation_ms": sum(s["duration_ms"] for s in event["stages"] if s["name"] == "generation"),
            "persistence_ms": sum(s["duration_ms"] for s in event["stages"] if s["name"] == "persistence"),
            "provider_ms": sum(c["elapsed_ms"] for c in event["provider_calls"]),
            "provider_calls": event["provider_call_attempts"],
            "covered_ms": event["stage_covered_ms"], "unexplained_ms": event["unexplained_ms"],
        }

    def test_synthetic_commit_delay_normal_stages_and_replay(self):
        commit = Session.commit
        delay = 0

        def controlled_commit(db):
            self.clock.advance(delay)
            return commit(db)

        with patch.object(metrics, "time", SimpleNamespace(perf_counter=self.clock.now)), \
                patch.object(Session, "commit", controlled_commit):
            baseline = self.post()
            delay = .3
            request_id = str(uuid.uuid4())
            delayed = self.post(request_id)
            replay = self.post(request_id)
        self.assertEqual((baseline.status_code, delayed.status_code, replay.status_code), (200, 200, 200))
        self.assertEqual(delayed.json(), replay.json())
        first, second, third = self.events
        self.assertEqual(self.values(first), {
            "server_ms": 50, "generation_ms": 50, "persistence_ms": 0,
            "provider_ms": 50, "provider_calls": 1, "covered_ms": 50, "unexplained_ms": 0,
        })
        self.assertEqual(self.values(second), {
            "server_ms": 350, "generation_ms": 50, "persistence_ms": 300,
            "provider_ms": 50, "provider_calls": 1, "covered_ms": 350, "unexplained_ms": 0,
        })
        names = [s["name"] for s in second["stages"]]
        self.assertEqual(set(names), {
            "coordination_preparation", "routing", "material_acquisition",
            "generation", "persistence", "cleanup",
        })
        generation = next(s for s in second["stages"] if s["name"] == "generation")
        persistence = next(s for s in second["stages"] if s["name"] == "persistence")
        self.assertEqual((generation["start_ms"], generation["end_ms"]), (0, 50))
        self.assertEqual((persistence["start_ms"], persistence["end_ms"]), (50, 350))
        self.assertTrue(all(s["outcome"] == "success" for s in second["stages"]))
        self.assertEqual(set(s["name"] for s in third["stages"]), {"coordination_preparation", "cleanup"})
        self.assertEqual(third["provider_call_attempts"], 0)
        self.assertEqual(third["execution_source"], "persisted_replay")
        self.assertEqual(self.generate.call_count, 2)
        with self.factory() as db:
            self.assertEqual(db.query(ChatTurn).count(), 2)
            self.assertEqual(db.query(Message).count(), 4)
        self.assertNotIn("PRIVATE", json.dumps(self.events))
        print("SYNTHETIC_COMMIT_DELAY", json.dumps({"baseline": self.values(first), "delay": self.values(second)}))

    def test_wall_clock_commit_delay(self):
        commit = Session.commit
        baseline = self.post()
        injected = []

        def delayed_commit(db):
            started = time.perf_counter()
            threading.Event().wait(.3)  # The requested injected delay, not a race synchronizer.
            injected.append((time.perf_counter() - started) * 1000)
            return commit(db)

        with patch.object(Session, "commit", delayed_commit):
            delayed = self.post()
        self.assertEqual((baseline.status_code, delayed.status_code), (200, 200))
        first, second = map(self.values, self.events)
        self.assertEqual((first["provider_calls"], second["provider_calls"]), (1, 1))
        for field in ("server_ms", "persistence_ms"):
            self.assertAlmostEqual(second[field] - first[field], injected[0], delta=150)
        for field in ("generation_ms", "provider_ms"):
            self.assertLess(abs(second[field] - first[field]), 150)
        self.assertGreaterEqual(second["persistence_ms"], 290)
        print("WALL_COMMIT_DELAY", json.dumps({"baseline": first, "delay": second, "injected_ms": injected[0]}))

    def test_exceptions_preserve_failed_and_completed_intervals(self):
        for target, expected in [
            ("owned_workspace", "coordination_preparation"),
            ("decide", "routing"), ("retrieve", "material_acquisition"),
            ("grounded_answer", "generation"),
        ]:
            with self.subTest(stage=expected), patch.object(
                chat, target, side_effect=HTTPException(503, "PRIVATE FAILURE")
            ):
                self.assertEqual(self.post().status_code, 503)
                stages = self.events[-1]["stages"]
                self.assertTrue(any(s["name"] == expected and s["outcome"] == "failure" for s in stages))
                self.assertEqual(stages[-1]["name"], "cleanup")
                self.assertFalse(any(s["name"] == "persistence" for s in stages))
        with patch.object(Session, "commit", side_effect=HTTPException(503, "PRIVATE COMMIT")):
            self.assertEqual(self.post().status_code, 503)
        stages = self.events[-1]["stages"]
        self.assertTrue(any(s["name"] == "generation" and s["outcome"] == "success" for s in stages))
        self.assertTrue(any(s["name"] == "persistence" and s["outcome"] == "failure" for s in stages))
        self.assertNotIn("PRIVATE", json.dumps(self.events))

    def test_no_material_stage_when_skipped_and_clock_failure_is_harmless(self):
        with patch.object(chat, "decide", return_value={"action": "direct", "reason": "test", "origin": "test"}):
            self.assertEqual(self.post().status_code, 200)
        self.assertNotIn("material_acquisition", [s["name"] for s in self.events[-1]["stages"]])
        # Fail just the new stage clock reads; existing HTTP/provider clocks still work.
        original = metrics.chat_stage
        from contextlib import contextmanager

        @contextmanager
        def broken_stage(name):
            with patch.object(metrics, "time", SimpleNamespace(perf_counter=Mock(side_effect=OSError("clock")))):
                manager = original(name)
                manager.__enter__()
            try:
                yield
            finally:
                manager.__exit__(None, None, None)

        with patch.object(chat, "chat_stage", broken_stage):
            self.assertEqual(self.post().status_code, 200)


class FollowerLatencyTests(unittest.TestCase):
    def test_timeout_and_result_failure_keep_only_executed_stages(self):
        fixture = test_singleflight_metrics.SingleflightHTTPMetricsTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        _, _, events = fixture.pair(local=True)
        result = next(s for s in events["follower"]["stages"] if s["name"] == "follower_result")
        self.assertEqual(result["outcome"], "failure")
        flight = fixture.active_flight()
        with patch.object(flight.done, "wait", return_value=False):
            self.assertEqual(fixture.post().status_code, 504)
        event = fixture.events[-1]
        self.assertNotIn("follower_result", [s["name"] for s in event["stages"]])
        wait = next(s for s in event["stages"] if s["name"] == "follower_wait")
        self.assertEqual(wait["outcome"], "failure")
        self.assertEqual(event["provider_call_attempts"], 0)

    def test_real_wait_and_result_stages(self):
        fixture = test_singleflight_metrics.SingleflightHTTPMetricsTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        _, _, events = fixture.pair()
        follower = events["follower"]
        self.assertEqual([s["name"] for s in follower["stages"]], [
            "coordination_preparation", "follower_preparation", "follower_wait", "cleanup", "follower_result",
        ])
        wait = next(s for s in follower["stages"] if s["name"] == "follower_wait")
        self.assertGreaterEqual(wait["duration_ms"] + .01, follower["follower_wait_ms"])
        self.assertEqual(follower["provider_call_attempts"], 0)
