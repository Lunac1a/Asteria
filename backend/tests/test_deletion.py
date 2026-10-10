"""Deletion uses isolated accounts, strict foreign keys, and synthetic files."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import test_dashboard
from sqlalchemy import text
from app.api.routes.chat import chat_slot
from app.api.routes.knowledge import document_slot
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
        self.temp = self.enterContext(tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2] / "data"))
        self.root = Path(self.temp)
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
        return self.client.delete("/api/" + path, headers=headers or self.headers)

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
        self.assertEqual(self.delete("workspaces/space").status_code, 204)
        self.assert_chat_removed()
        self.assertFalse(self.file.exists())
        with self.factory() as db:
            self.assertEqual(db.query(Chunk).count(), 0)
            self.assertEqual(db.query(Document).count(), 0)
            self.assertIsNone(db.get(Workspace, "space"))
            self.assertIsNotNone(db.get(Workspace, "other"))

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
