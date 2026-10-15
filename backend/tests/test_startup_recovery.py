"""Lifespan/gate tests; real protocol startup paths use the isolated PG fixture."""
import threading
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import text

import test_workspace_delete_recovery as protocol
from app.core.config import settings
from app.main import create_app
from app.services import startup_recovery as startup
from app.services import workspace_delete_recovery as recovery


class StartupRecoveryTests(unittest.TestCase):
    setUp = protocol.WorkspaceDeleteRecoveryTests.setUp
    prepare = protocol.WorkspaceDeleteRecoveryTests.prepare
    stage = protocol.WorkspaceDeleteRecoveryTests.stage
    operation = protocol.WorkspaceDeleteRecoveryTests.operation
    staged = protocol.WorkspaceDeleteRecoveryTests.staged
    assert_restored = protocol.WorkspaceDeleteRecoveryTests.assert_restored
    assert_cleaned = protocol.WorkspaceDeleteRecoveryTests.assert_cleaned

    def app(self):
        self.enterContext(patch.object(settings, "STARTUP_RECOVERY_ENABLED", True))
        self.enterContext(patch.object(settings, "LOCAL_DATA_DIR", str(self.root)))
        self.enterContext(patch("app.main.engine", self.bind))
        app = create_app()
        app.dependency_overrides.update(self.client.app.dependency_overrides)
        return app

    def pg(self):
        if not self.postgres:
            self.skipTest("Requires explicitly isolated PostgreSQL")

    def test_no_pending_startup_and_repeated_startup(self):
        self.pg()
        for _ in range(2):
            with TestClient(self.app()) as client:
                self.assertEqual(client.get("/api/ready").status_code, 200)
                self.assertEqual(client.get("/api/health").status_code, 200)

    def test_prepared_startup(self):
        self.pg()
        identifier = self.prepare()
        self.stage(identifier)
        with TestClient(self.app()) as client:
            self.assertEqual(client.get("/api/ready").status_code, 200)
            self.assert_restored()
        with patch.object(recovery, "apply_files", side_effect=AssertionError("terminal file action")):
            with TestClient(self.app()) as client:
                self.assertEqual(client.get("/api/ready").status_code, 200)

    def test_committed_startup(self):
        self.pg()
        identifier = self.prepare()
        with recovery.operation_gates():
            recovery.delete_transaction(self.bind, identifier, self.root)
        with TestClient(self.app()) as client:
            self.assertEqual(client.get("/api/ready").status_code, 200)
            self.assert_cleaned()

    def test_enabled_startup_allows_controlled_http_delete(self):
        self.pg()
        with TestClient(self.app()) as client:
            self.assertEqual(client.get("/api/ready").status_code, 200)
            response = client.delete("/api/workspaces/workspace-a", headers=self.users[0][1])
            self.assertEqual(response.status_code, 204)
            self.assert_cleaned()
            self.assertEqual(client.get("/api/ready").status_code, 200)

    def test_conflict_protects_every_business_endpoint(self):
        self.pg()
        identifier = self.prepare()
        self.stage(identifier)
        self.paths[0].write_bytes(b"Do not overwrite")
        with TestClient(self.app()) as client:
            self.assertEqual(client.get("/api/health").status_code, 200)
            self.assertEqual(client.get("/api/ready").status_code, 503)
            for endpoint in ("/api/me", "/api/dashboard", "/api/workspaces", "/api/db-health"):
                self.assertEqual(client.get(endpoint).status_code, 503)
            self.assertEqual(client.post("/api/admin/document-recovery/execute", json={}).status_code, 503)
        self.assertEqual(self.paths[0].read_bytes(), b"Do not overwrite")
        self.assertTrue(all(p.exists() for p in self.staged(self.operation())))
        self.assertEqual(self.operation().state, "PREPARED")

    def test_missing_schema_stays_not_ready(self):
        self.pg()
        with self.bind.begin() as db:
            db.execute(text("DROP TABLE workspace_delete_operations"))
        with TestClient(self.app()) as client:
            self.assertEqual(client.get("/api/ready").status_code, 503)
        self.assertTrue(all(p.exists() for p in self.paths))

    def test_postgresql_required(self):
        if self.postgres:
            self.skipTest("SQLite rejection test")
        with TestClient(self.app()) as client:
            self.assertEqual(client.get("/api/ready").status_code, 503)

    def test_failure_does_not_release_gate_or_lease(self):
        with patch("app.main.recover_startup", side_effect=RuntimeError("synthetic unavailable")):
            with TestClient(self.app()) as client:
                self.assertEqual(client.get("/api/ready").status_code, 503)
                self.assertEqual(client.get("/api/health").status_code, 200)
                with self.assertRaises(OSError), startup.storage_lease(self.root):
                    pass
        with startup.storage_lease(self.root):
            pass

    def test_attestation_required_before_recovery(self):
        with patch.object(settings, "RECOVERY_SINGLE_PROCESS_CONFIRMED", False):
            with patch("app.main.recover_startup") as recover:
                with TestClient(self.app()) as client:
                    self.assertEqual(client.get("/api/ready").status_code, 503)
                recover.assert_not_called()

    def test_no_lifespan_cannot_open_business(self):
        app = self.app()
        client = TestClient(app)
        self.addCleanup(client.close)
        self.assertEqual(client.get("/api/me").status_code, 503)

    def test_startup_barrier_blocks_requests(self):
        app = self.app()
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()

        def blocked(*args):
            entered.set()
            if not release.wait(10):
                raise RuntimeError("test barrier timed out")

        def start():
            try:
                with TestClient(app):
                    finished.set()
                    release.wait(10)
            finally:
                finished.set()

        with patch("app.main.recover_startup", side_effect=blocked):
            thread = threading.Thread(target=start)
            thread.start()
            try:
                self.assertTrue(entered.wait(5))
                client = TestClient(app)  # Deliberately bypass lifespan to probe the gate.
                try:
                    self.assertEqual(client.get("/api/ready").status_code, 503)
                    self.assertEqual(client.get("/api/workspaces").status_code, 503)
                finally:
                    client.close()
            finally:
                release.set()
                thread.join(10)
        self.assertTrue(finished.is_set())

    def test_runtime_pending_is_not_automatically_recovered(self):
        self.pg()
        with TestClient(self.app()) as client:
            self.assertEqual(client.get("/api/ready").status_code, 200)
            identifier = self.prepare()
            self.assertEqual(client.get("/api/ready").status_code, 503)
            self.assertEqual(self.operation().state, "PREPARED")
            with recovery.operation_gates():
                recovery.delete_transaction(self.bind, identifier, self.root)
            recovery.recover_operation(self.bind, identifier, self.root)
            self.assertEqual(client.get("/api/ready").status_code, 200)
