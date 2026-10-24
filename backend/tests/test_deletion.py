"""Deletion uses isolated accounts, strict foreign keys, and synthetic files."""
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
import test_dashboard
from sqlalchemy import select, text
from app.api.routes.chat import chat_slot
from app.api.routes.knowledge import document_slot
from app.core.config import settings
from app.models.chat_sessions import ChatSession
from app.models.messages import Message
from app.models.knowledge import Workspace, Document, Chunk, SessionWorkspace, MessageEvidence
from app.models.chat_turn import ChatTurn
from app.models.session_profile import SessionProfile
from app.models.learning import LearningContext, LearningRecap, RecapDraft


class DeletionTests(unittest.TestCase):
    def setUp(self):
        test_dashboard.DashboardTests.setUp(self)
        with self.factory() as db:
            db.execute(text("PRAGMA foreign_keys=ON"))
        self.temp = self.enterContext(tempfile.TemporaryDirectory(prefix="asteria-deletion-"))
        self.root = Path(self.temp).resolve()
        self.enterContext(patch("app.api.routes.knowledge.data_root", return_value=self.root))
        (self.root / "uploads").mkdir()
        self.file = self.root / "uploads" / "reading.txt"
        self.file.write_text("Synthetic reading", encoding="utf-8")
        owner, self.headers = self.users[0]
        with self.factory() as db:
            db.add_all([Workspace(id="space", user_id=owner, name="Synthetic"), Workspace(id="other", user_id=self.users[1][0], name="Private")])
            db.flush()
            db.add(Document(id="doc", workspace_id="space", name="Reading", storage_key="uploads/reading.txt", size_bytes=17, status="ready", embedding_model="mock"))
            db.add(ChatSession(id="chat", user_id=owner, title="Synthetic"))
            db.flush()
            db.add_all([Chunk(id="chunk", document_id="doc", ordinal=0, content="Synthetic", vector=[]), SessionWorkspace(session_id="chat", workspace_id="space"), SessionProfile(session_id="chat", session_type="learning"), LearningContext(session_id="chat"), Message(id="message", session_id="chat", role="assistant", content="Synthetic"), ChatTurn(request_id="turn", user_id=owner, session_id="chat", question="Synthetic", response={}), LearningRecap(id="recap", session_id="chat", cycle=1, content={}, snapshot={}), RecapDraft(id="draft", session_id="chat", cycle=1, context_revision=1, content={}, snapshot={})])
            db.flush()
            db.add(MessageEvidence(message_id="message", sources=[], ai_mode="mock"))
            db.commit()

    def delete(self, path, headers=None):
        with patch.object(settings, "WORKSPACE_DELETE_MAINTENANCE_MODE", path.startswith("workspaces/")), patch.object(settings, "RECOVERY_SINGLE_PROCESS_CONFIRMED", True):
            return self.client.delete("/api/" + path, headers=headers or self.headers)

    def persisted_state(self):
        # A fresh Session reads committed column values, not cached ORM objects.
        with self.factory() as db:
            database = {
                table.name: [dict(row) for row in db.execute(
                    select(table).order_by(*table.primary_key.columns)
                ).mappings()]
                for table in test_dashboard.Base.metadata.sorted_tables
            }
        files = {
            path.relative_to(self.root).as_posix(): path.read_bytes() if path.is_file() else None
            for path in self.root.rglob("*")
        }
        return database, files

    def assert_chat_removed(self):
        with self.factory() as db:
            for model in (ChatSession, Message, MessageEvidence, ChatTurn, SessionWorkspace, SessionProfile, LearningContext, LearningRecap, RecapDraft):
                self.assertEqual(db.query(model).count(), 0, model.__name__)

    def test_conversation_cleanup_preserves_materials(self):
        self.assertEqual(self.delete("chat/sessions/chat").status_code, 204)
        self.assert_chat_removed()
        self.assertTrue(self.file.exists())
        with self.factory() as db:
            self.assertIsNotNone(db.get(Document, "doc"))
            self.assertIsNotNone(db.get(Workspace, "space"))
        self.assertEqual(self.delete("chat/sessions/chat").status_code, 404)

    def test_workspace_cleanup_is_scoped(self):
        before, _ = self.persisted_state()
        response = self.delete("workspaces/space")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        self.assert_chat_removed()
        self.assertFalse(self.file.exists())
        with self.factory() as db:
            self.assertEqual(db.query(Chunk).count(), 0)
            self.assertEqual(db.query(Document).count(), 0)
            self.assertIsNone(db.get(Workspace, "space"))
            self.assertIsNotNone(db.get(Workspace, "other"))
        after, files = self.persisted_state()
        self.assertEqual(after["workspaces"], [row for row in before["workspaces"] if row["id"] == "other"])
        self.assertEqual(after["users"], before["users"])
        # No original or staged document file remains after successful deletion.
        self.assertTrue(all(content is None for content in files.values()))

    def test_workspace_unauthorized_delete_preserves_all_state(self):
        # Both tokens are obtained by the fixture's real registration/login flow.
        for user_id, headers in self.users:
            response = self.client.get("/api/me", headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["id"], str(user_id))
        with self.factory() as db:
            self.assertEqual(db.get(Workspace, "space").user_id, self.users[0][0])
            self.assertEqual(db.get(Workspace, "other").user_id, self.users[1][0])
            self.assertNotEqual(self.users[0][0], self.users[1][0])
        before = self.persisted_state()
        self.assertEqual(before[1]["uploads/reading.txt"], b"Synthetic reading")

        response = self.delete("workspaces/space", self.users[1][1])

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Workspace not found"})
        # Includes Workspace.user_id, Documents, Chunks, conversations, evidence,
        # learning records and users, plus file bytes and directory entries.
        self.assertEqual(self.persisted_state(), before)

    def test_workspace_missing_delete_preserves_all_state(self):
        missing_id = str(uuid.uuid4())
        with self.factory() as db:
            self.assertIsNone(db.get(Workspace, missing_id))
        before = self.persisted_state()
        for user_id, headers in self.users:
            with self.subTest(user=str(user_id)):
                response = self.delete(f"workspaces/{missing_id}", headers)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json(), {"detail": "Workspace not found"})
                self.assertEqual(self.persisted_state(), before)

    def test_other_user_cannot_delete(self):
        for path in ("workspaces/space", "chat/sessions/chat"):
            self.assertEqual(self.delete(path, self.users[1][1]).status_code, 404)
            self.assertEqual(self.client.delete("/api/" + path).status_code, 401)
        self.assertTrue(self.file.exists())

    def test_busy_generation_and_upload_are_protected(self):
        with chat_slot:
            self.assertEqual(self.delete("workspaces/space").status_code, 409)
            self.assertEqual(self.delete("chat/sessions/chat").status_code, 409)
        with document_slot:
            self.assertEqual(self.delete("workspaces/space").status_code, 409)
        self.assertTrue(self.file.exists())
        self.assertEqual(self.delete("workspaces/space").status_code, 204)

    def test_processing_and_invalid_paths_preserve_data(self):
        with self.factory() as db:
            db.get(Document, "doc").status = "processing"
            db.commit()
        self.assertEqual(self.delete("workspaces/space").status_code, 409)
        with self.factory() as db:
            doc = db.get(Document, "doc")
            doc.status, doc.storage_key = "ready", "../outside.txt"
            db.commit()
        self.assertEqual(self.delete("workspaces/space").status_code, 409)
        self.assertTrue(self.file.exists())

    def test_commit_failure_restores_records_and_file(self):
        from sqlalchemy.orm import Session
        with patch.object(Session, "commit", side_effect=RuntimeError("Synthetic failure")):
            with self.assertRaises(RuntimeError):
                self.delete("workspaces/space")
        self.assertEqual(self.file.read_text(encoding="utf-8"), "Synthetic reading")
        with self.factory() as db:
            self.assertIsNotNone(db.get(Workspace, "space"))
            self.assertIsNotNone(db.get(ChatSession, "chat"))
        self.assertEqual(self.delete("workspaces/space").status_code, 204)

    def test_missing_file_can_be_deleted(self):
        self.file.unlink()
        self.assertEqual(self.delete("workspaces/space").status_code, 204)

    def test_inconsistent_foreign_binding_is_not_deleted(self):
        with self.factory() as db:
            db.get(ChatSession, "chat").user_id = self.users[1][0]
            db.commit()
        self.assertEqual(self.delete("workspaces/space").status_code, 409)
        self.assertEqual(self.delete("chat/sessions/chat", self.users[1][1]).status_code, 404)
        self.assertTrue(self.file.exists())
