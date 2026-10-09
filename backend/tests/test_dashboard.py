"""Dashboard ownership, exact aggregates and deterministic recency."""
import unittest
import uuid
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from test_auth import Base, get_db
from app.main import create_app
from app.models.chat_sessions import ChatSession
from app.models.knowledge import Workspace, Document, SessionWorkspace


class DashboardTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        self.factory = sessionmaker(bind=engine)
        def database():
            with self.factory() as db:
                yield db
        app = create_app()
        app.dependency_overrides[get_db] = database
        self.client = self.enterContext(TestClient(app))
        self.users = []
        for email in ["dashboard-a@example.com", "dashboard-b@example.com"]:
            body = {"email": email, "password": "Synthetic-Test-42"}
            user = self.client.post("/api/register", json=body).json()
            token = self.client.post("/api/login", json=body).json()["access_token"]
            self.users.append((uuid.UUID(user["id"]), {"Authorization": "Bearer " + token}))

    def test_empty_and_authentication(self):
        self.assertEqual(self.client.get("/api/dashboard").status_code, 401)
        result = self.client.get("/api/dashboard", headers=self.users[0][1])
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), {"workspaces": [], "conversations": []})

    def test_counts_recency_and_both_ownership_boundaries(self):
        owner, headers = self.users[0]
        other, _ = self.users[1]
        now = datetime.now(timezone.utc)
        with self.factory() as db:
            db.add_all([Workspace(id="a", user_id=owner, name="Actual name"), Workspace(id="b", user_id=other, name="Private")])
            db.flush()
            for index, status in enumerate(["ready", "processing", "failed"]):
                db.add(Document(id=f"d{index}", workspace_id="a", name=str(index), storage_key="unused", size_bytes=1, status=status, embedding_model="unused"))
            for index in range(8):
                db.add(ChatSession(id=f"c{index}", user_id=owner, title=f"Conversation {index}", updated_at=now + timedelta(seconds=index)))
            # Legacy global sessions and inconsistent foreign bindings must not leak.
            db.add_all([ChatSession(id="global", user_id=owner, title="Unbound"), ChatSession(id="wrong-user", user_id=other, title="Private"), ChatSession(id="wrong-space", user_id=owner, title="Private workspace")])
            db.flush()
            db.add_all([SessionWorkspace(session_id=f"c{i}", workspace_id="a") for i in range(8)])
            db.add_all([SessionWorkspace(session_id="wrong-user", workspace_id="a"), SessionWorkspace(session_id="wrong-space", workspace_id="b")])
            db.commit()
        result = self.client.get("/api/dashboard", headers=headers).json()
        self.assertEqual(result["workspaces"], [{"id": "a", "name": "Actual name", "material_count": 3, "conversation_count": 8}])
        self.assertEqual([row["id"] for row in result["conversations"]], [f"c{i}" for i in range(7, 1, -1)])
        self.assertTrue(all(row["workspace_name"] == "Actual name" for row in result["conversations"]))
        self.assertNotIn("learning_status", result["conversations"][0])
