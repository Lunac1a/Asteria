"""Event assertions on the existing recovery fixtures, with no model/provider."""
import json
import threading
import unittest
from unittest.mock import patch

import test_query_supervisor as fixtures
import test_restart_policy as policy
from app.services.knowledge import lifecycle_events as events
from app.services.knowledge.query_supervisor import QueryWorkerSupervisor
from app.services import telemetry_writer as delivery
from app.core.config import settings


class EventRecoveryTests(fixtures.QuerySupervisorTests):
    def setUp(self):
        self.records = []
        capture = patch.object(events, "emit", side_effect=lambda item: self.records.append(events.sanitize(item)))
        capture.start()
        self.addCleanup(capture.stop)

    def tearDown(self):
        names = [r["event_type"] for r in self.records]
        self.assertIn("initialization_started", names)
        self.assertIn("shutdown_completed", names)
        self.assertEqual([r["sequence"] for r in self.records], list(range(1, len(self.records)+1)))
        if "cleanup_uncertainty" in self.id():
            self.assertIn("cleanup_unconfirmed", names)
            self.assertIn("supervisor_degraded", names)
            self.assertNotIn("restart_attempted", names)
            self.assertLess(names.index("cleanup_unconfirmed"), names.index("cleanup_confirmed"))
        if "budget_is_bounded" in self.id():
            manual = next(r for r in self.records if r["event_type"] == "manual_required")
            self.assertEqual(manual["reason_code"], "failed_cycle_limit")
            self.assertEqual(manual["attempt"], 2)

    def test_pid_reuse_and_recovery_order(self):
        def factory():
            worker = fixtures.ControlledWorker()
            worker.pid = 42
            return worker
        s = self.make(factory)
        s.start()
        self.crash(s)
        self.wait(s, lambda: s.state == "READY" and s.restarts == 1)
        s.close()
        ready = [r for r in self.records if r["event_type"] == "initialization_completed"]
        self.assertEqual([r["pid"] for r in ready], [42, 42])
        self.assertNotEqual(ready[0]["worker_instance_id"], ready[1]["worker_instance_id"])
        names = [r["event_type"] for r in self.records]
        expected = ["failure_detected", "cleanup_started", "cleanup_confirmed", "restart_scheduled",
                    "initialization_started", "restart_attempted", "initialization_completed", "restart_completed"]
        start = names.index("failure_detected")
        self.assertEqual(names[start:start+len(expected)], expected)

    def test_restart_disabled_reason(self):
        s = self.make(fixtures.ControlledWorker, restart_limit=0)
        try:
            s.start()
            self.crash(s)
            self.wait(s, lambda: s.state == "MANUAL_REQUIRED")
        finally:
            s.close()
        row = next(r for r in self.records if r["event_type"] == "manual_required")
        self.assertEqual(row["reason_code"], "restart_disabled")
        self.assertEqual(row["attempt"], 0)


class EventPolicyTests(policy.RestartPolicyTests):
    def setUp(self):
        super().setUp()
        self.records = []
        capture = patch.object(events, "emit", side_effect=lambda item: self.records.append(events.sanitize(item)))
        capture.start()
        self.addCleanup(capture.stop)

    def tearDown(self):
        for row in self.records:
            if row["event_type"] == "manual_required":
                self.assertIn(row["reason_code"], {"restart_disabled", "failed_cycle_limit"})
            if row["event_type"] == "cooldown_entered":
                self.assertEqual(row["new_state"], "COOLDOWN")
                self.assertEqual(row["reason_code"], "restart_budget")
        if "three_cycles" in self.id():
            self.assertEqual(sum(r["event_type"] == "cooldown_entered" for r in self.records), 2)
            self.assertEqual(sum(r["event_type"] == "manual_required" for r in self.records), 1)


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(settings, "LOCAL_DATA_DIR", folder))
        self.enterContext(patch.object(settings, "RUNTIME_METRICS_ENABLED", True))
        self.writer = delivery.TelemetryWriter()
        self.enterContext(patch.object(delivery, "writer", self.writer))
        self.addCleanup(lambda: self.assertTrue(self.writer.close(3)))

    def test_slow_writer_does_not_block_supervisor(self):
        entered, release = threading.Event(), threading.Event()
        def slow_writer(path, line):
            entered.set()
            release.wait(5)
        with patch.object(delivery, "_write", side_effect=slow_writer):
            s = QueryWorkerSupervisor("unused", ".", threading.BoundedSemaphore(1),
                                      worker_factory=fixtures.ControlledWorker, backoff_seconds=.01)
            try:
                s.start()
                self.assertTrue(entered.wait(1))
                with s.condition:
                    s.worker.alive = False
                    s.condition.notify_all()
                    self.assertTrue(s.condition.wait_for(lambda: s.restarts == 1 and s.state == "READY", 2))
                s.close()
                self.assertFalse(release.is_set())
            finally:
                release.set()
                s.close()
                self.assertTrue(self.writer.flush(3))

    def test_schema_rejects_sensitive_fields_and_unknown_reasons(self):
        from datetime import datetime, timezone
        row = events.sanitize(dict(event_type="failure_detected", reason_code="secret prompt",
                                   timestamp_utc=datetime.now(timezone.utc).isoformat(),
                                   prompt="secret prompt", error="secret key", stderr="private document",
                                   budget={"limit": 3, "secret": "credential"}))
        self.assertEqual(row["reason_code"], "unknown")
        self.assertEqual(row["budget"], {"limit": 3})
        self.assertNotIn("secret", json.dumps(row))
        self.assertNotIn("private", json.dumps(row))

    def test_writer_failure_and_sensitive_exception_exclusion(self):
        written = []
        secret = "SECRET_API_KEY_PRIVATE_PROMPT_DOCUMENT"
        def bad_factory():
            raise RuntimeError(secret)
        def broken_writer(path, line):
            written.append(json.loads(line))
            raise OSError(secret)
        with patch.object(delivery, "_write", side_effect=broken_writer):
            s = QueryWorkerSupervisor("unused", ".", threading.BoundedSemaphore(1), worker_factory=bad_factory)
            with self.assertRaises(RuntimeError):
                s.start()
            s.close()
            self.assertTrue(self.writer.flush(3))
        self.assertEqual(s.state, "STOPPED")
        self.assertNotIn(secret, json.dumps(written))
        self.assertIn("initialization_failed", [r["event_type"] for r in written])

    def test_emitter_failure_does_not_prevent_recovery(self):
        s = QueryWorkerSupervisor("unused", ".", threading.BoundedSemaphore(1),
                                  worker_factory=fixtures.ControlledWorker, backoff_seconds=.01)
        with patch.object(events, "emit", side_effect=OSError("sensitive")):
            try:
                s.start()
                with s.condition:
                    s.worker.alive = False
                    s.condition.notify_all()
                    self.assertTrue(s.condition.wait_for(lambda: s.restarts == 1 and s.state == "READY", 3))
            finally:
                s.close()
        self.assertEqual(s.state, "STOPPED")

    def test_full_queue_is_nonblocking(self):
        with patch.object(self.writer, "submit", return_value=False) as submit:
            with patch.object(events, "sanitize", return_value={}):
                events.emit({})
            submit.assert_called_once()
