"""Manual recovery permissions, locking, fresh reads, and retry regressions."""

import unittest
import threading
import uuid
from unittest.mock import patch

import test_index_unexpected_exception as fixtures
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from jose import jwt

from app.api.routes.knowledge import document_slot
from app.core.config import settings
from app.core.security import create_access_token
from app.db.session import get_db
from app.main import create_app
from app.models.knowledge import Chunk, Document
from app.models.users import User


class DocumentRecoveryTests(unittest.TestCase):
    def setUp(self):
        with patch.object(fixtures, "create_engine", side_effect=lambda *a, **k: create_engine(
            *a, connect_args={"check_same_thread": False}, **k
        )):
            fixtures.UnexpectedIndexExceptionTests.setUp(self)
        fixtures.UnexpectedIndexExceptionTests.seed_document(self, "processing", old_chunk=True)
        with self.factory() as db:
            db.get(Document, "document").chunk_count = 99
            db.commit()
        self.enterContext(patch.object(settings, "RECOVERY_ADMIN_USER_IDS", [str(self.owner)]))
        self.enterContext(patch.object(settings, "RECOVERY_SINGLE_PROCESS_CONFIRMED", True))
        app = create_app()

        def isolated_db():
            with self.factory() as db:
                yield db

        app.dependency_overrides[get_db] = isolated_db
        self.client = self.enterContext(TestClient(app))
        self.headers = {"Authorization": "Bearer " + create_access_token({"sub": str(self.owner)})}

    def preview(self):
        response = self.client.post("/api/admin/document-recovery/preview",
                                    json={"document_ids": ["document"]}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["preview_token"]

    def execute(self, token, confirm=True):
        return self.client.post("/api/admin/document-recovery/execute",
                                json={"preview_token": token, "confirm": confirm}, headers=self.headers)

    def state(self):
        with self.factory() as fresh:
            d = fresh.get(Document, "document")
            chunks = [(c.id, c.content, c.vector) for c in fresh.query(Chunk).all()]
            return d.status, d.error, d.chunk_count, chunks

    def test_preview_is_read_only_recovery_preserves_chunks_and_repeat_is_safe(self):
        before = self.state()
        token = self.preview()
        self.assertEqual(self.state(), before)
        self.assertEqual(self.execute(token).json(), {"recovered": ["document"], "skipped": []})
        after = self.state()
        self.assertEqual(after[:3], ("failed", "Previous indexing did not complete. Please reindex this document.", 1))
        self.assertEqual(after[3], before[3])
        self.assertEqual(self.execute(token).json(), {"recovered": [], "skipped": ["document"]})
        self.assertEqual(self.state(), after)

    def test_running_document_operation_blocks_preview_and_execute(self):
        token, before = self.preview(), self.state()
        document_slot.acquire()
        try:
            self.assertEqual(self.execute(token).status_code, 409)
            response = self.client.post("/api/admin/document-recovery/preview",
                                        json={"document_ids": ["document"]}, headers=self.headers)
            self.assertEqual(response.status_code, 409)
        finally:
            document_slot.release()
        self.assertEqual(self.state(), before)

    def test_ready_after_preview_is_skipped(self):
        token = self.preview()
        with self.factory() as db:
            db.get(Document, "document").status = "ready"
            db.commit()
        before = self.state()
        self.assertEqual(self.execute(token).json(), {"recovered": [], "skipped": ["document"]})
        self.assertEqual(self.state(), before)

    def test_actual_reindex_worker_blocks_recovery_until_complete(self):
        token = self.preview()
        with self.factory() as db:
            db.get(Document, "document").status = "failed"
            db.commit()
        started, finish = threading.Event(), threading.Event()
        results = []

        def worker(*args):
            started.set()
            if not finish.wait(5):
                raise RuntimeError("Synthetic worker wait expired")
            return {"chunks": [dict(page=None, content="Completed", vector=[1.0])]}

        def run():
            results.append(self.client.post("/api/workspaces/workspace/documents/document/reindex",
                                            headers=self.headers))

        with patch("app.api.routes.knowledge.run_worker", side_effect=worker):
            thread = threading.Thread(target=run)
            thread.start()
            try:
                self.assertTrue(started.wait(5))
                self.assertEqual(self.execute(token).status_code, 409)
            finally:
                finish.set()
                thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0].status_code, 200)
        self.assertEqual(self.state()[:3], ("ready", None, 1))

    def test_recovery_then_existing_reindex_endpoint_recovers_ready(self):
        self.assertEqual(self.execute(self.preview()).status_code, 200)
        with patch("app.api.routes.knowledge.run_worker", return_value={"chunks": [
            dict(page=None, content="Recovered material", vector=[0.0, 1.0]),
        ]}):
            response = self.client.post("/api/workspaces/workspace/documents/document/reindex", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.state()[:3], ("ready", None, 1))
        self.assertEqual(self.state()[3][0][1], "Recovered material")

    def test_unauthenticated_and_ordinary_users_cannot_recover(self):
        token, before = self.preview(), self.state()
        self.assertEqual(self.client.post("/api/admin/document-recovery/execute",
                                         json={"preview_token": token, "confirm": True}).status_code, 401)
        with patch.object(settings, "RECOVERY_ADMIN_USER_IDS", []):
            self.assertEqual(self.execute(token).status_code, 403)
            self.assertEqual(self.client.post("/api/admin/document-recovery/preview",
                                             json={"document_ids": ["document"]}, headers=self.headers).status_code, 403)
        self.assertEqual(self.state(), before)

    def test_single_process_confirmation_and_manual_confirmation_required(self):
        token, before = self.preview(), self.state()
        with patch.object(settings, "RECOVERY_SINGLE_PROCESS_CONFIRMED", False):
            self.assertEqual(self.execute(token).status_code, 409)
        self.assertEqual(self.execute(token, confirm=False).status_code, 422)
        self.assertEqual(self.state(), before)

    def test_tampered_and_previous_process_preview_rejected(self):
        token, before = self.preview(), self.state()
        self.assertEqual(self.execute("invalid-token").status_code, 400)
        with patch("app.api.routes.document_recovery.process_id", "different-process"):
            self.assertEqual(self.execute(token).status_code, 400)
        self.assertEqual(self.state(), before)

    def test_commit_failure_rolls_back_and_releases_gate(self):
        token, before = self.preview(), self.state()
        from sqlalchemy.orm import Session
        with patch.object(Session, "commit", side_effect=RuntimeError("Synthetic commit failure")):
            with self.assertRaisesRegex(RuntimeError, "Synthetic commit failure"):
                self.execute(token)
        self.assertEqual(self.state(), before)
        self.assertTrue(document_slot.acquire(blocking=False))
        document_slot.release()

    def test_expired_or_wrong_purpose_preview_cannot_write(self):
        token, before = self.preview(), self.state()
        claims = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        for change in ({"exp": 0}, {"purpose": "other-operation"}):
            with self.subTest(change=change):
                invalid = jwt.encode({**claims, **change}, settings.JWT_SECRET_KEY,
                                     algorithm=settings.JWT_ALGORITHM)
                self.assertEqual(self.execute(invalid).status_code, 400)
                self.assertEqual(self.state(), before)

    def test_another_authorized_admin_cannot_use_preview(self):
        token, before = self.preview(), self.state()
        other = uuid.uuid4()
        with self.factory() as db:
            db.add(User(id=other, email="other-recovery-admin@example.com", password_hash="synthetic"))
            db.commit()
        with patch.object(settings, "RECOVERY_ADMIN_USER_IDS", [str(self.owner), str(other)]):
            response = self.client.post("/api/admin/document-recovery/execute",
                                        json={"preview_token": token, "confirm": True},
                                        headers={"Authorization": "Bearer " + create_access_token({"sub": str(other)})})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.state(), before)
