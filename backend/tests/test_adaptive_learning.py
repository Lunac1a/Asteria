"""Deterministic engineering contracts. Provider behavior tested separately with real calls."""

import uuid
import unittest
from unittest.mock import patch
from fastapi import HTTPException
import test_dashboard
from app.core.config import settings
from app.models.knowledge import Workspace
from app.models.messages import Message
from app.models.learning import LearningContext
from app.api.routes.chat import chat_slot

CONTENT = dict(
    explored="Discussed cost estimates.",
    tried="A user comparison.",
    unclear="No specific difficulty recorded; admissibility not yet explored.",
    next="Optionally try an example.",
)


class AdaptiveTests(unittest.TestCase):
    def setUp(self):
        test_dashboard.DashboardTests.setUp(self)
        self.enterContext(
            patch(
                "app.services.smart_routing.provider_completion",
                return_value='{"intent":"general"}',
            )
        )
        self.headers = self.users[0][1]
        with self.factory() as db:
            db.add(
                Workspace(
                    id="learn", name="Learning contract", user_id=self.users[0][0]
                )
            )
            db.commit()
        self.enterContext(patch("app.api.routes.chat.retrieve", return_value=[]))
        self.generate = self.enterContext(
            patch("app.api.routes.chat.grounded_answer", side_effect=self.answer)
        )
        self.recap = self.enterContext(
            patch("app.api.routes.learning.generate_recap", return_value=CONTENT)
        )
        first = self.send("I want to understand A*", kind="learning")
        self.sid = first.json()["session_id"]
        self.path = f"/api/chat/sessions/{self.sid}/learning"

    def answer(self, db, user, question, history, sources, *args, **kwargs):
        if "learning_update" in kwargs:
            kwargs["learning_update"]["value"] = dict(
                focus="Search costs",
                next_step="Compare estimates",
                evidence=(
                    dict(
                        kind="attempt",
                        text="You compared path costs in this attempt.",
                        quote=question,
                    )
                    if question.startswith("I think")
                    else None
                ),
            )
        return "A saved explanation", [], "general"

    def send(self, text, kind=None, sid=None):
        return self.client.post(
            "/api/chat",
            headers=self.headers,
            json=dict(
                message=text,
                workspace_id="learn",
                session_type=kind,
                session_id=sid,
                request_id=str(uuid.uuid4()),
            ),
        )

    def get(self):
        return self.client.get(self.path, headers=self.headers).json()

    def post(self, action, body):
        return self.client.post(self.path + action, headers=self.headers, json=body)

    def draft(self):
        state = self.get()
        return self.post(
            "/recap-draft",
            dict(request_id=str(uuid.uuid4()), revision=state["revision"]),
        )

    def finish(self, draft=None, skip=False):
        state = self.get()
        return self.post(
            "/finish",
            dict(
                revision=state["revision"],
                cycle=state["cycle"],
                draft_id=draft and draft["id"],
                content=draft and draft["content"],
                without_recap=skip,
            ),
        )

    def test_evidence_quotes_user_edits_and_context_continuity(self):
        self.assertEqual(self.get()["evidence"], [])
        self.send("I think h(n)=0 means f(n)=g(n)", sid=self.sid)
        state = self.get()
        self.assertEqual(len(state["evidence"]), 1)
        item = state["evidence"][0]
        with self.factory() as db:
            self.assertIn(item["quote"], db.get(Message, item["message_id"]).content)
        edit = {
            k: state[k]
            for k in ["revision", "goal", "focus", "notes", "next_step", "evidence"]
        }
        edit.update(
            goal="A user-confirmed new goal",
            notes="Please do not infer mastery",
            evidence=[],
        )
        self.assertEqual(
            self.client.put(
                self.path + "/notes", headers=self.headers, json=edit
            ).status_code,
            200,
        )
        self.send("Give me the answer directly; no quiz", sid=self.sid)
        context = self.generate.call_args.kwargs["learning_context"]
        self.assertEqual(context["goal"], edit["goal"])
        self.assertEqual(context["notes"], edit["notes"])
        self.assertEqual(context["evidence"], [])
        self.assertEqual(
            self.client.put(
                self.path + "/notes", headers=self.headers, json=edit
            ).status_code,
            409,
        )

    def test_invalid_metadata_does_not_discard_chat_or_saved_notes(self):
        with self.factory() as db:
            ctx = db.get(LearningContext, self.sid)
            ctx.notes = "Keep this correction"
            db.commit()

        def invalid(*args, **kwargs):
            kwargs["learning_update"]["value"] = dict(
                focus="Invented",
                evidence=dict(
                    kind="attempt", text="Unfounded", quote="Not in user message"
                ),
            )
            return "Actual answer saved", [], "general"

        self.generate.side_effect = invalid
        self.assertEqual(self.send("Hello", sid=self.sid).status_code, 200)
        state = self.get()
        self.assertTrue(state["update_warning"])
        self.assertEqual(state["notes"], "Keep this correction")
        self.assertEqual(state["evidence"], [])

    def test_preview_cancel_failure_finish_resume_history_and_readonly(self):
        original = self.get()
        self.recap.side_effect = HTTPException(502, "Recap failed")
        self.assertEqual(self.draft().status_code, 502)
        self.assertEqual(self.get(), original)
        self.recap.side_effect = None
        draft = self.draft().json()
        self.assertEqual(
            self.get()["status"], "active"
        )  # cancel is read-only, draft preserved
        self.assertEqual(self.get()["drafts"][0]["id"], draft["id"])
        draft["content"]["tried"] = "User corrected this recap"
        done = self.finish(draft).json()
        self.assertEqual(done["status"], "finished")
        self.assertEqual(
            done["recaps"][0]["content"]["tried"], "User corrected this recap"
        )
        self.assertEqual(self.send("Another message", sid=self.sid).status_code, 409)
        self.assertEqual(self.finish(draft).status_code, 200)
        self.assertEqual(len(self.get()["recaps"]), 1)
        resumed = self.post("/resume", dict(revision=done["revision"])).json()
        self.assertEqual(resumed["cycle"], 2)
        self.send("I think the remaining cost is an estimate", sid=self.sid)
        again = self.finish(self.draft().json()).json()
        self.assertEqual(len(again["recaps"]), 2)
        self.assertEqual(
            again["recaps"][1]["content"]["tried"], "User corrected this recap"
        )

    def test_finished_without_recap_and_later_snapshot(self):
        done = self.finish(skip=True).json()
        row = done["recaps"][0]
        self.assertIsNone(row["content"])
        self.post("/resume", dict(revision=done["revision"]))
        self.send("A later topic", sid=self.sid)
        draft = self.post(
            "/recap-draft",
            dict(
                request_id=str(uuid.uuid4()),
                revision=self.get()["revision"],
                target_recap=row["id"],
            ),
        )
        self.assertEqual(draft.status_code, 200)
        passed = self.recap.call_args.args[2]
        self.assertFalse(
            any(m["content"] == "A later topic" for m in passed["messages"])
        )
        saved = self.client.put(
            self.path + f"/recaps/{row['id']}",
            headers=self.headers,
            json=dict(revision=row["revision"], content=CONTENT),
        ).json()
        self.assertEqual(saved["status"], "active")
        self.assertIsNotNone(saved["recaps"][0]["content"])

    def test_ownership_questioning_and_quote_validation(self):
        self.assertEqual(
            self.client.get(self.path, headers=self.users[1][1]).status_code, 404
        )
        q = self.send("Simple question", kind="questioning").json()["session_id"]
        self.assertEqual(
            self.client.get(
                f"/api/chat/sessions/{q}/learning", headers=self.headers
            ).status_code,
            404,
        )
        with self.factory() as db:
            self.assertIsNone(db.get(LearningContext, q))
        state = self.get()
        edit = {
            k: state[k]
            for k in ["revision", "goal", "focus", "notes", "next_step", "evidence"]
        }
        edit["evidence"] = [
            dict(kind="attempt", text="fake", quote="fake", message_id="foreign")
        ]
        self.assertEqual(
            self.client.put(
                self.path + "/notes", headers=self.headers, json=edit
            ).status_code,
            422,
        )
        self.assertEqual(self.get()["evidence"], [])

    def test_stale_draft_and_busy_mutations_preserve_records(self):
        draft = self.draft().json()
        self.send("More discussion", sid=self.sid)
        self.assertEqual(self.finish(draft).status_code, 409)
        self.assertEqual(self.get()["status"], "active")
        self.assertEqual(self.get()["recaps"], [])
        chat_slot.acquire()
        try:
            self.assertEqual(
                self.post("/resume", dict(revision=self.get()["revision"])).status_code,
                409,
            )
        finally:
            chat_slot.release()

    def test_save_failure_transaction_preserves_active_and_draft(self):
        draft = self.draft().json()
        before = self.get()
        from sqlalchemy.orm import Session

        with patch.object(
            Session, "commit", side_effect=RuntimeError("Injected save failure")
        ):
            with self.assertRaises(RuntimeError):
                self.finish(draft)
        after = self.get()
        self.assertEqual(after, before)
        self.assertEqual(self.finish(draft).json()["status"], "finished")

    def test_dashboard_prioritizes_active_learning_over_newer_questioning(self):
        self.send("Newer question", kind="questioning")
        dash = self.client.get("/api/dashboard", headers=self.headers).json()
        self.assertEqual(dash["continue_learning"]["id"], self.sid)
        self.finish(skip=True)
        dash = self.client.get("/api/dashboard", headers=self.headers).json()
        self.assertNotIn("continue_learning", dash)

    def test_generation_categories_do_not_mutate_learning_or_recap_drafts(self):
        self.enterContext(patch.object(settings, "LLM_BACKEND", "provider"))
        from app.services.generation_protocol import GenerationError, failure
        from app.services.rag_service import grounded_answer

        draft = self.draft().json()
        before = self.get()
        with self.factory() as db:
            messages_before = db.query(Message).filter_by(session_id=self.sid).count()
        self.generate.side_effect = grounded_answer
        failures = [
            "json_parse",
            "schema_validation",
            "citation_validation",
            "output_truncated",
            "provider_timeout",
        ]
        for category in failures:
            with (
                self.subTest(category=category),
                patch(
                    "app.services.rag_service.provider_completion",
                    side_effect=failure(GenerationError(category)),
                ),
            ):
                result = self.send("Synthetic failed request", sid=self.sid)
                self.assertEqual(
                    result.status_code, 504 if category == "provider_timeout" else 502
                )
                self.assertEqual(self.get(), before)
            self.recap.side_effect = failure(GenerationError(category), "recap")
            self.assertIn(self.draft().status_code, [502, 504])
            self.assertEqual(self.get(), before)
        with self.factory() as db:
            self.assertEqual(
                db.query(Message).filter_by(session_id=self.sid).count(),
                messages_before,
            )
        self.assertEqual(self.get()["drafts"][0]["id"], draft["id"])
        self.recap.side_effect = None
        draft["content"]["tried"] = "Correction retained after failed generation"
        done = self.finish(draft).json()
        self.assertEqual(done["status"], "finished")
        self.assertEqual(
            done["recaps"][0]["content"]["tried"], draft["content"]["tried"]
        )
        resumed = self.post("/resume", dict(revision=done["revision"])).json()
        self.assertEqual(resumed["status"], "active")
        self.assertEqual(len(resumed["recaps"]), 1)
