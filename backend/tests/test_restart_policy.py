"""Rolling restart policy with a controlled monotonic clock, no sleeps."""
import threading
import unittest

from test_query_supervisor import ControlledWorker
from fastapi import HTTPException
from app.services.knowledge.query_supervisor import QueryWorkerSupervisor


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance_to(self, supervisor, value):
        with supervisor.condition:
            assert value >= self.now
            self.now = value
            supervisor.condition.notify_all()


class RestartPolicyTests(unittest.TestCase):
    def setUp(self):
        self.evidence = []
        self.clock = Clock()
        self.workers = []

    def make(self, fail=lambda index: False):
        def factory():
            worker = ControlledWorker(start_error=fail(len(self.workers)))
            self.workers.append(worker)
            return worker
        supervisor = QueryWorkerSupervisor("unused", ".", threading.BoundedSemaphore(1),
            worker_factory=factory, clock=self.clock, restart_limit=1, window_seconds=10,
            failed_cycle_limit=3, backoff_seconds=1, startup_timeout=1, shutdown_timeout=.1)
        self.supervisor = supervisor
        def cleanup():
            for worker in self.workers:
                worker.cleanup_error = False
            supervisor.close()
        self.addCleanup(cleanup)
        supervisor.start()
        return supervisor

    def wait(self, predicate):
        s = self.supervisor
        with s.condition:
            self.assertTrue(s.condition.wait_for(predicate, 5), s.snapshot())

    def record(self):
        self.evidence.append({"clock": self.clock(), **self.supervisor.snapshot(), "created": len(self.workers)})

    def crash(self):
        with self.supervisor.condition:
            self.supervisor.worker.alive = False
            self.supervisor.condition.notify_all()

    def attempt(self):
        self.wait(lambda: self.supervisor.retry_at is not None)
        self.clock.advance_to(self.supervisor, self.supervisor.retry_at)

    def reject(self):
        with self.assertRaises(HTTPException) as error:
            self.supervisor.embed("query", 1)
        self.assertEqual(error.exception.status_code, 503)

    def test_sporadic_failures_do_not_permanently_exhaust_budget(self):
        s = self.make()
        for count in range(1, 6):
            self.crash()
            self.attempt()
            self.wait(lambda: s.state == "READY" and s.restarts == count)
            self.record()
            self.clock.advance_to(s, self.clock() + 11)
            self.wait(lambda: s.cycle_attempts == 0)
        self.assertEqual(s.restarts, 5)
        self.assertEqual(s.failed_cycles, 0)
        self.assertEqual(s.embed("new query", 1), [1.0, 0.0])

    def test_cooldown_autonomously_recovers_once_then_manual_after_three_cycles(self):
        s = self.make(fail=lambda i: i > 0)
        self.crash()
        for cycle in range(1, 4):
            self.attempt()
            expected = "COOLDOWN" if cycle < 3 else "MANUAL_REQUIRED"
            self.wait(lambda: s.state == expected)
            self.assertEqual(s.failed_cycles, cycle)
            self.assertEqual(s.restarts, cycle)
            self.record()
            for _ in range(8):
                self.reject()
            self.assertEqual(len(self.workers), cycle + 1)
            if cycle < 3:
                deadline = s.cooldown_until
                self.clock.advance_to(s, deadline - .01)
                self.reject()
                self.assertEqual(s.state, "COOLDOWN")
                # No request triggers recovery: only clock + scheduler notification.
                self.clock.advance_to(s, deadline)
        self.clock.advance_to(s, self.clock() + 10000)
        self.reject()
        self.assertEqual(s.state, "MANUAL_REQUIRED")
        self.assertEqual(s.restarts, 3)

    def test_stable_full_window_resets_failed_cycles(self):
        s = self.make(fail=lambda i: i == 1)
        self.crash()
        self.attempt()
        self.wait(lambda: s.state == "COOLDOWN")
        self.clock.advance_to(s, s.cooldown_until)
        self.attempt()
        self.wait(lambda: s.state == "READY")
        self.assertEqual(s.failed_cycles, 1)
        self.clock.advance_to(s, s.ready_since + 9.99)
        self.assertEqual(s.failed_cycles, 1)
        self.clock.advance_to(s, s.ready_since + 10)
        self.wait(lambda: s.failed_cycles == 0)
        self.record()

    def test_time_cannot_clear_degraded_ownership(self):
        s = self.make()
        worker = s.worker
        worker.cleanup_error = True
        worker.gate_held = True
        self.crash()
        self.wait(lambda: s.state == "DEGRADED")
        self.clock.advance_to(s, self.clock() + 10000)
        self.reject()
        self.assertIs(s.worker, worker)
        self.assertIsNotNone(worker.job)
        self.assertTrue(worker.gate_held)
        self.assertEqual(s.restarts, 0)
        self.record()

    def test_slow_failed_initializers_cannot_evade_manual_limit(self):
        s = self.make(fail=lambda i: i > 0)
        original_factory = s.factory
        def slow_factory():
            worker = original_factory()
            start = worker.start
            def slow_start():
                self.clock.advance_to(s, self.clock() + 11)
                start()
            worker.start = slow_start
            return worker
        s.factory = slow_factory
        self.crash()
        for cycle in range(1, 4):
            self.attempt()
            self.wait(lambda: s.state == ("COOLDOWN" if cycle < 3 else "MANUAL_REQUIRED"))
            self.record()
            if cycle < 3:
                self.clock.advance_to(s, s.cooldown_until)
        self.assertEqual(s.restarts, 3)
        self.assertEqual(s.state, "MANUAL_REQUIRED")
