"""Session identity/atomicity regressions; generation is an explicit test double."""
import unittest
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from fastapi import HTTPException
import test_dashboard
from app.models.chat_sessions import ChatSession
from app.models.session_profile import SessionProfile
from app.models.messages import Message
from app.models.knowledge import Workspace, SessionWorkspace


class SessionFoundationTests(unittest.TestCase):
    def setUp(self):
        test_dashboard.DashboardTests.setUp(self)
        self.enterContext(patch("app.services.smart_routing.provider_completion", return_value='{"intent":"general"}'))
        self.headers = self.users[0][1]
        with self.factory() as db:
            db.add_all([Workspace(id="owned", name="Session tests", user_id=self.users[0][0]),
                        Workspace(id="second", name="Other workspace", user_id=self.users[0][0])])
            db.commit()
        self.enterContext(patch("app.api.routes.chat.retrieve", return_value=[]))
        self.generate = self.enterContext(patch("app.api.routes.chat.grounded_answer", return_value=("Saved test response", [], "general")))

    def send(self, **changes):
        payload = dict(message="Understand search heuristics", workspace_id="owned", session_type="learning", request_id=str(uuid.uuid4()))
        payload.update(changes)
        return self.client.post("/api/chat", headers=self.headers, json=payload)

    def test_type_goal_and_recovery_are_persistent_and_immutable(self):
        request_id = str(uuid.uuid4())
        result = self.send(request_id=request_id)
        self.assertEqual(result.status_code, 200)
        answer = result.json()
        self.assertEqual(answer["session_type"], "learning")
        self.assertEqual(answer["learning_goal"], "Understand search heuristics")
        session_id = answer["session_id"]
        self.assertEqual(self.send(request_id=request_id).json(), answer)
        self.assertEqual(self.generate.call_count, 1)
        recovered = self.client.get(f"/api/chat/turns/{request_id}", headers=self.headers).json()
        self.assertEqual(recovered, answer)
        followup = self.send(session_id=session_id, message="Explain another example")
        self.assertEqual(followup.json()["learning_goal"], answer["learning_goal"])
        self.assertEqual(self.send(session_id=session_id, session_type="questioning").status_code, 409)
        self.assertEqual(self.send(request_id=request_id, session_type="questioning").status_code, 409)
        self.assertEqual(self.send(session_id=session_id, workspace_id="second").status_code, 404)
        rows = self.client.get("/api/chat/sessions?workspace_id=owned", headers=self.headers).json()
        self.assertEqual(rows[0]["session_type"], "learning")
        self.assertEqual(rows[0]["learning_goal"], answer["learning_goal"])
        with self.factory() as db:
            self.assertEqual(db.query(ChatSession).count(), 1)
            self.assertEqual(db.query(SessionProfile).count(), 1)
            self.assertEqual(db.query(Message).count(), 4)
        foreign = self.client.get(f"/api/chat/sessions/{session_id}/messages", headers=self.users[1][1])
        self.assertEqual(foreign.status_code, 404)

    def test_first_turn_failure_leaves_no_empty_session_then_same_request_recovers(self):
        request_id = str(uuid.uuid4())
        self.generate.side_effect = HTTPException(503, "Provider unavailable")
        self.assertEqual(self.send(request_id=request_id).status_code, 503)
        with self.factory() as db:
            self.assertEqual(db.query(ChatSession).count(), 0)
            self.assertEqual(db.query(SessionProfile).count(), 0)
            self.assertEqual(db.query(Message).count(), 0)
        self.generate.side_effect = None
        self.assertEqual(self.send(request_id=request_id).status_code, 200)
        self.assertEqual(self.send(request_id=request_id).status_code, 200)
        with self.factory() as db:
            self.assertEqual(db.query(ChatSession).count(), 1)
            self.assertEqual(db.query(Message).count(), 2)

    def test_questioning_has_no_goal_and_legacy_is_not_reclassified(self):
        result = self.send(session_type="questioning").json()
        self.assertEqual(result["session_type"], "questioning")
        self.assertIsNone(result["learning_goal"])
        with self.factory() as db:
            db.add(ChatSession(id="legacy", title="Old conversation", user_id=self.users[0][0]))
            db.flush()
            db.add(SessionWorkspace(session_id="legacy", workspace_id="owned"))
            db.commit()
        result = self.send(session_id="legacy", session_type=None).json()
        self.assertIsNone(result["session_type"])
        self.assertEqual(self.send(session_id="legacy", session_type="learning").status_code, 409)

    def test_long_history_pages_are_complete_ordered_and_owned(self):
        now = datetime.now(timezone.utc)
        with self.factory() as db:
            db.add(ChatSession(id="long", title="Long history", user_id=self.users[0][0]))
            db.flush()
            db.add(SessionWorkspace(session_id="long", workspace_id="owned"))
            db.add_all([Message(id=f"m{i:03}", session_id="long", role="user" if i%2==0 else "assistant", content=f"Message {i}", created_at=now+timedelta(seconds=i)) for i in range(220)])
            db.commit()
        ids=[]
        for offset in range(0,250,50):
            result=self.client.get(f"/api/chat/sessions/long/messages?offset={offset}&limit=50",headers=self.headers)
            self.assertEqual(result.status_code,200)
            ids=[row["id"] for row in result.json()]+ids
        self.assertEqual(ids,[f"m{i:03}" for i in range(220)])
        self.assertEqual(self.client.get("/api/chat/sessions/long/messages",headers=self.users[1][1]).status_code,404)
        self.assertEqual(self.client.get("/api/chat/sessions/long/messages?limit=201",headers=self.headers).status_code,422)
