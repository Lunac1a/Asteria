"""Synthetic protocol and fail-closed lifecycle regressions; no model or network."""
from contextlib import nullcontext
import threading
import unittest
from unittest.mock import Mock, patch

import test_auth  # noqa: F401 -- isolated settings before app imports
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.core.config import settings
from app.main import create_app
from app.services.knowledge.persistent_query import PersistentQueryWorker


class PersistentQueryTests(unittest.TestCase):
    def worker(self):
        worker = PersistentQueryWorker("model", "unused", threading.BoundedSemaphore(1), shutdown_timeout=.02)
        worker.state = "READY"
        worker.job = Mock()
        worker.job.pids.return_value = []
        worker.job.accounting.return_value = {"active_processes": 0}
        worker.native = Mock()
        return worker

    def test_task_identity_and_slot_release(self):
        worker = self.worker()
        ids = []
        def exchange(payload, timeout):
            ids.append(payload["task_id"])
            return {"type": "result", "task_id": payload["task_id"], "vectors": [[.5, .2]]}
        worker._exchange = exchange
        for _ in range(3):
            self.assertEqual(worker.embed("question", 1), [.5, .2])
        self.assertEqual(len(set(ids)), 3)
        self.assertTrue(worker.gate.acquire(False))
        worker.gate.release()
        worker.close()

    def test_indexer_gate_remains_shared_and_fail_fast(self):
        worker = self.worker()
        worker.gate.acquire()
        try:
            with self.assertRaises(HTTPException) as error:
                worker.embed("question", 1)
            self.assertEqual(error.exception.status_code, 429)
            self.assertEqual(worker.state, "READY")
        finally:
            worker.gate.release()
            worker.close()

    def test_wrong_identity_and_invalid_vectors_fail_without_retry(self):
        for response in ({"type": "result", "task_id": "wrong", "vectors": [[1.0]]},
                         {"type": "result", "vectors": [[float("nan")]]}):
            worker = self.worker()
            worker._exchange = Mock(return_value=response)
            with self.assertRaises(HTTPException) as error:
                worker.embed("question", 1)
            self.assertEqual(error.exception.status_code, 503)
            self.assertEqual(worker.state, "FAILED")
            with self.assertRaises(HTTPException):
                worker.embed("question", 1)
            worker._exchange.assert_called_once()
            self.assertFalse(worker.gate_held)

    def test_unconfirmed_cleanup_retains_slot(self):
        worker = self.worker()
        job = worker.job
        job.accounting.return_value = {"active_processes": 1}
        worker._exchange = Mock(side_effect=TimeoutError("injected"))
        with self.assertRaises(HTTPException):
            worker.embed("question", .01)
        self.assertEqual(worker.state, "DEGRADED")
        self.assertTrue(worker.gate_held)
        self.assertFalse(worker.gate.acquire(False))
        job.close.assert_not_called()
        job.accounting.return_value = {"active_processes": 0}
        worker.close()
        self.assertEqual(worker.state, "STOPPED")
        self.assertFalse(worker.gate_held)

    def test_empty_job_with_unsignaled_member_retains_slot(self):
        worker = self.worker()
        job = worker.job
        job.pids.return_value = [123]
        job.contains.return_value = True
        worker.native.process_handle.return_value = 456
        worker.native.exit_status.return_value = {"exited": False}
        worker._exchange = Mock(side_effect=BrokenPipeError("dead child"))
        with self.assertRaises(HTTPException):
            worker.embed("question", 1)
        self.assertEqual(worker.state, "DEGRADED")
        self.assertEqual(worker.member_handles, {123: 456})
        self.assertTrue(worker.gate_held)
        job.close.assert_not_called()
        worker.native.close.assert_not_called()
        worker.native.exit_status.return_value = {"exited": True}
        worker.close()
        self.assertEqual(worker.member_handles, {})
        self.assertFalse(worker.gate_held)
        worker.native.close.assert_called_once_with(456)

    def test_dead_worker_pipe_flush_does_not_retain_slot(self):
        worker = self.worker()
        worker.stdin = Mock()
        worker.stdin.close.side_effect = OSError(22, "Invalid argument")
        worker.stdin.closed = True
        worker._exchange = Mock(side_effect=BrokenPipeError("dead child"))
        with self.assertRaises(HTTPException):
            worker.embed("question", 1)
        self.assertEqual(worker.state, "FAILED")
        self.assertFalse(worker.gate_held)
        self.assertIsNone(worker.job)

    def test_blocked_pipe_has_finite_deadline(self):
        worker = self.worker()
        release = threading.Event()
        worker.stdout = Mock()
        worker.stdout.readline.side_effect = lambda limit: (release.wait(2), "")[1]
        try:
            with self.assertRaises(TimeoutError):
                worker._exchange(None, .02)
        finally:
            release.set()
            worker.io_thread.join(2)
            worker.close()
        self.assertFalse(worker.io_thread.is_alive())

    def test_default_lifespan_never_creates_model_worker(self):
        with patch.object(settings, "PERSISTENT_QUERY_WORKER_ENABLED", False), patch(
                "app.main.QueryWorkerSupervisor") as factory:
            with TestClient(create_app()) as client:
                self.assertEqual(client.get("/api/health").status_code, 200)
            factory.assert_not_called()

    def test_enabled_start_failure_aborts_startup_and_closes(self):
        with patch.object(settings, "PERSISTENT_QUERY_WORKER_ENABLED", True), patch.object(
                settings, "EMBEDDING_BACKEND", "fastembed"), patch("app.main.data_root") as root, patch(
                "app.main.QueryWorkerSupervisor") as factory:
            root.return_value = __import__("pathlib").Path("unused")
            factory.return_value.start.side_effect = TimeoutError("init timeout")
            app = create_app()
            with self.assertRaises(TimeoutError), TestClient(app):
                pass
            self.assertFalse(app.state.recovery_ready)
            factory.return_value.close.assert_called_once()

    def test_recovery_failure_prevents_worker_start(self):
        with patch.object(settings, "PERSISTENT_QUERY_WORKER_ENABLED", True), patch.object(
                settings, "STARTUP_RECOVERY_ENABLED", True), patch(
                "app.services.workspace_delete_recovery.require_maintenance"), patch(
                "app.main.storage_lease", return_value=nullcontext()), patch(
                "app.main.recover_startup", side_effect=RuntimeError("recovery unavailable")), patch(
                "app.main.QueryWorkerSupervisor") as factory:
            with TestClient(create_app()) as client:
                self.assertEqual(client.get("/api/ready").status_code, 503)
                self.assertEqual(client.get("/api/workspaces").status_code, 503)
                self.assertEqual(client.get("/api/health").status_code, 200)
            factory.assert_not_called()

    def test_unsupported_backend_rejected(self):
        with patch.object(settings, "PERSISTENT_QUERY_WORKER_ENABLED", True):
            with self.assertRaisesRegex(RuntimeError, "fastembed"), TestClient(create_app()):
                pass

    def test_unsupported_platform_rejected(self):
        worker = PersistentQueryWorker("model", "unused", threading.BoundedSemaphore(1))
        with patch("app.services.knowledge.persistent_query.os.name", "posix"):
            with self.assertRaisesRegex(RuntimeError, "Windows"):
                worker.start()
