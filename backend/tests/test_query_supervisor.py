"""Deterministic supervisor transitions; no model, database or paid provider."""
import concurrent.futures
import threading
import time
import unittest

import test_auth  # noqa: F401
from fastapi import HTTPException
from app.services.knowledge.query_supervisor import QueryWorkerSupervisor


class ControlledWorker:
    def __init__(self, *, start_release=None, start_error=False):
        self.state = "NEW"
        self.pid = id(self)
        self.job = object()
        self.gate_held = False
        self.alive = True
        self.start_release = start_release
        self.started = threading.Event()
        self.start_error = start_error
        self.cleanup_error = False
        self.closed_at = None

    def start(self):
        self.start_time = time.monotonic()
        self.started.set()
        if self.start_release and not self.start_release.wait(5):
            raise TimeoutError("fixture release missing")
        if self.start_error:
            self.state = "FAILED"
            raise RuntimeError("deterministic initialization failure")
        self.state = "READY"

    def is_alive(self):
        return self.alive

    def embed(self, question, timeout):
        return [1.0, 0.0]

    def close(self):
        if self.cleanup_error:
            self.state = "DEGRADED"
            raise RuntimeError("exit unconfirmed")
        self.job = None
        self.gate_held = False
        self.state = "STOPPED"
        self.closed_at = time.monotonic()


class QuerySupervisorTests(unittest.TestCase):
    def wait(self, supervisor, predicate):
        with supervisor.condition:
            self.assertTrue(supervisor.condition.wait_for(predicate, 5), supervisor.snapshot())

    def make(self, factory, **kwargs):
        return QueryWorkerSupervisor("unused", ".", threading.BoundedSemaphore(1),
            worker_factory=factory, startup_timeout=1, shutdown_timeout=.1,
            backoff_seconds=.01, **kwargs)

    def crash(self, supervisor):
        with supervisor.condition:
            supervisor.worker.alive = False
            supervisor.condition.notify_all()

    def test_single_flight_initialization_outside_lock_and_no_request_retry(self):
        release = threading.Event()
        workers = []
        def factory():
            worker = ControlledWorker(start_release=release if workers else None)
            workers.append(worker)
            return worker
        supervisor = self.make(factory)
        try:
            supervisor.start()
            self.crash(supervisor)
            barrier = threading.Barrier(8)
            def request():
                barrier.wait(3)
                try:
                    supervisor.embed("query", 1)
                except HTTPException as exc:
                    return exc.status_code
            with concurrent.futures.ThreadPoolExecutor(8) as pool:
                futures = [pool.submit(request) for _ in range(8)]
                self.assertEqual([f.result(4) for f in futures], [503] * 8)
            self.wait(supervisor, lambda: len(workers) == 2 and supervisor.state == "STARTING")
            self.assertTrue(workers[1].started.wait(3))
            # If start held the condition, these calls would block behind release.
            self.assertEqual(supervisor.snapshot()["state"], "STARTING")
            self.assertIsNone(workers[0].job)
            self.assertGreaterEqual(workers[1].start_time - workers[0].closed_at, .009)
            self.assertEqual(supervisor.restarts, 1)
            release.set()
            self.wait(supervisor, lambda: supervisor.state == "READY")
            self.assertEqual(supervisor.embed("new query", 1), [1.0, 0.0])
            self.assertEqual(len(workers), 2)
        finally:
            release.set()
            supervisor.close()
        self.assertEqual(supervisor.state, "STOPPED")

    def test_restart_budget_is_bounded_and_requests_cannot_reset_it(self):
        workers = []
        def factory():
            worker = ControlledWorker(start_error=bool(workers))
            workers.append(worker)
            return worker
        supervisor = self.make(factory, restart_limit=2, failed_cycle_limit=1)
        try:
            supervisor.start()
            self.crash(supervisor)
            self.wait(supervisor, lambda: supervisor.exhausted)
            self.assertEqual(supervisor.state, "MANUAL_REQUIRED")
            self.assertEqual(supervisor.restarts, 2)
            self.assertEqual(len(workers), 3)
            self.assertTrue(all(w.job is None for w in workers))
            for _ in range(10):
                with self.assertRaises(HTTPException) as error:
                    supervisor.embed("query", 1)
                self.assertEqual(error.exception.status_code, 503)
            self.assertEqual(len(workers), 3)
        finally:
            supervisor.close()

    def test_cleanup_uncertainty_is_terminal_and_retains_ownership(self):
        worker = ControlledWorker()
        supervisor = self.make(lambda: worker)
        try:
            supervisor.start()
            worker.cleanup_error = True
            worker.gate_held = True
            self.crash(supervisor)
            self.wait(supervisor, lambda: supervisor.state == "DEGRADED")
            self.assertIs(supervisor.worker, worker)
            self.assertIsNotNone(worker.job)
            self.assertTrue(worker.gate_held)
            self.assertEqual(supervisor.restarts, 0)
            with self.assertRaises(HTTPException) as error:
                supervisor.embed("query", 1)
            self.assertEqual(error.exception.status_code, 503)
        finally:
            worker.cleanup_error = False  # Test teardown only, no runtime recovery.
            supervisor.close()

    def test_shutdown_during_initialization_does_not_publish_ready(self):
        release = threading.Event()
        first, second = ControlledWorker(), ControlledWorker(start_release=release)
        workers = iter([first, second])
        supervisor = self.make(lambda: next(workers))
        supervisor.start()
        self.crash(supervisor)
        self.assertTrue(second.started.wait(3))
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            future = pool.submit(supervisor.close)
            self.assertTrue(supervisor.stop.wait(3))
            with self.assertRaises(HTTPException):
                supervisor.embed("query", 1)
            release.set()
            future.result(5)
        self.assertIsNone(second.job)
        self.assertEqual(supervisor.state, "STOPPED")

    def test_initial_start_failure_does_not_retry(self):
        worker = ControlledWorker(start_error=True)
        supervisor = self.make(lambda: worker)
        try:
            with self.assertRaises(RuntimeError):
                supervisor.start()
            self.assertTrue(supervisor.exhausted)
            self.assertEqual(supervisor.restarts, 0)
        finally:
            supervisor.close()

    def test_brief_ready_does_not_reset_rolling_budget(self):
        workers = []
        def factory():
            worker = ControlledWorker()
            workers.append(worker)
            return worker
        supervisor = self.make(factory, restart_limit=2, failed_cycle_limit=1)
        try:
            supervisor.start()
            for attempt in (1, 2):
                self.crash(supervisor)
                self.wait(supervisor, lambda: supervisor.state == "READY" and supervisor.restarts == attempt)
            self.crash(supervisor)
            self.wait(supervisor, lambda: supervisor.exhausted)
            self.assertEqual(len(workers), 3)
            self.assertEqual(supervisor.state, "MANUAL_REQUIRED")
            self.assertTrue(all(worker.job is None for worker in workers))
        finally:
            supervisor.close()

    def test_shutdown_interrupts_backoff_without_starting_replacement(self):
        workers = []
        def factory():
            worker = ControlledWorker()
            workers.append(worker)
            return worker
        supervisor = self.make(factory)
        supervisor.backoff_seconds = 30
        supervisor.start()
        self.crash(supervisor)
        self.wait(supervisor, lambda: supervisor.retry_at is not None and supervisor.state == "FAILED")
        supervisor.close()
        self.assertEqual(len(workers), 1)
        self.assertEqual(supervisor.state, "STOPPED")
