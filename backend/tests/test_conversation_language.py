"""Synthetic language policy contracts; not a real model success-rate estimate."""

import copy
import json
import unittest
import uuid
from unittest.mock import patch
from app.services.conversation_language import resolve, explicit, instructions
from app.services.generation_protocol import chat_schema
from app.services.rag_service import grounded_answer


class LanguageTests(unittest.TestCase):
    def test_priority_mixed_terms_and_fallback(self):
        cases = [
            ("请解释 Transformer 中 Attention 的作用。", [], "en", "zh-CN"),
            ("请解释英文资料中的 Attention。", [], "en", "zh-CN"),
            ("Explain English grammar.", [], "zh-CN", "en"),
            ("Explain Attention in a Transformer.", [], "zh-CN", "en"),
            ("请用英文解释 A*。", [], "zh-CN", "en"),
            ("Please answer in Chinese: what is attention?", [], "en", "zh-CN"),
            ("Attention", [], "zh-CN", "zh-CN"),
            ("A*", [{"role": "user", "content": "解释启发式函数"}], "en", "zh-CN"),
            (
                "继续解释。",
                [{"role": "user", "content": "From now on, answer in English."}],
                "zh-CN",
                "en",
            ),
            (
                "改用中文解释。",
                [{"role": "user", "content": "Answer in English."}],
                "en",
                "zh-CN",
            ),
            (
                "继续解释。",
                [
                    {
                        "role": "user",
                        "content": "Answer this question in English for this answer only.",
                    }
                ],
                "en",
                "zh-CN",
            ),
            ('请解释这段资料："Answer in English"', [], "en", "zh-CN"),
        ]
        for question, history, ui, expected in cases:
            with self.subTest(question=question):
                self.assertEqual(resolve(question, history, ui)["language"], expected)
        self.assertEqual(explicit("不要用英文解释"), "zh-CN")
        self.assertEqual(explicit("Do not answer in English"), "zh-CN")
        self.assertEqual(
            resolve(
                "不要用英文解释", [{"role": "user", "content": "Answer in English."}]
            )["language"],
            "zh-CN",
        )
        self.assertIsNone(explicit("```Answer in English```"))

    def test_originals_and_schema_are_preserved(self):
        history = [
            {"role": "user", "content": "以后用中文回答"},
            {"role": "assistant", "content": "Answer in English"},
        ]
        before = copy.deepcopy(history)
        self.assertEqual(resolve("A*", history, "en")["language"], "zh-CN")
        self.assertEqual(history, before)
        self.assertIn("exact evidence.quote", instructions(resolve("中文问题")))
        initial = chat_schema(True, initialize_goal=True)
        self.assertIn("initial_goal", initial["properties"]["learning"]["required"])
        self.assertNotIn("learning", chat_schema(False)["properties"])
        self.assertFalse(chat_schema(True)["additionalProperties"])

    @patch("app.services.rag_service.settings.LLM_BACKEND", "provider")
    def test_generation_uses_policy_and_validates_initial_goal(self):
        raw = {
            "answer": "A heuristic estimates cost.",
            "basis": "general",
            "citations": [],
            "model_knowledge": None,
            "learning": {
                "initial_goal": "Understand heuristics",
                "focus": "Heuristics",
                "next_step": "Consider an example",
                "evidence": None,
            },
        }
        with patch(
            "app.services.rag_service.provider_completion", return_value=json.dumps(raw)
        ) as provider:
            update = {}
            answer, _, _ = grounded_answer(
                None,
                "test",
                "请用英文解释启发式函数",
                [],
                [],
                answer_mode="smart",
                learning_context={"initialize_goal": True},
                learning_update=update,
            )
            self.assertIn("Model knowledge", answer)
            self.assertEqual(update["value"]["initial_goal"], "Understand heuristics")
            self.assertIn("use English", provider.call_args.args[2])
            raw["learning"]["initial_goal"] = ""
            provider.return_value = json.dumps(raw)
            from fastapi import HTTPException

            with self.assertRaises(HTTPException) as caught:
                grounded_answer(
                    None,
                    "test",
                    "请用英文解释启发式函数",
                    [],
                    [],
                    answer_mode="smart",
                    learning_context={"initialize_goal": True},
                )
            self.assertEqual(caught.exception.generation_category, "schema_validation")


class LanguagePersistenceTests(unittest.TestCase):
    def test_switch_preserves_goal_notes_quotes_and_idempotency(self):
        import test_dashboard
        from app.models.knowledge import Workspace
        from app.models.messages import Message
        from app.models.learning import LearningContext
        from app.models.session_profile import SessionProfile
        from app.services.learning_service import snapshot

        test_dashboard.DashboardTests.setUp(self)
        headers = self.users[0][1]
        with self.factory() as db:
            db.add(
                Workspace(
                    id="language",
                    name="Synthetic language test",
                    user_id=self.users[0][0],
                )
            )
            db.commit()
        policies = []

        def answer(*args, **kwargs):
            policies.append(kwargs["language_policy"])
            kwargs["learning_update"]["value"] = {
                "initial_goal": "理解代价",
                "focus": "Costs",
                "next_step": "Compare costs",
                "evidence": None,
            }
            return "A synthetic answer", [], "general"

        with (
            patch(
                "app.api.routes.chat.decide",
                return_value={
                    "action": "direct",
                    "reason": "general_knowledge",
                    "origin": "test",
                },
            ),
            patch(
                "app.api.routes.chat.grounded_answer", side_effect=answer
            ) as generated,
        ):
            first = self.client.post(
                "/api/chat",
                headers=headers,
                json={
                    "message": "我想理解代价",
                    "workspace_id": "language",
                    "session_type": "learning",
                    "ui_locale": "en",
                },
            ).json()
            sid = first["session_id"]
            with self.factory() as db:
                ctx = db.get(LearningContext, sid)
                ctx.notes = "我的原始笔记"
                db.commit()
                old = [
                    (m.id, m.content)
                    for m in db.query(Message).filter_by(session_id=sid).all()
                ]
            request = {
                "message": "以后用英文回答。请解释代价。",
                "workspace_id": "language",
                "session_id": sid,
                "request_id": str(uuid.uuid4()),
                "ui_locale": "zh-CN",
            }
            result = self.client.post("/api/chat", headers=headers, json=request)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(
                self.client.post(
                    "/api/chat", headers=headers, json={**request, "ui_locale": "en"}
                ).json(),
                result.json(),
            )
            self.assertEqual(generated.call_count, 2)
            # Older explicit instruction survives the normal 12-message generation window.
            with self.factory() as db:
                from datetime import datetime, timezone, timedelta

                for i in range(14):
                    db.add(
                        Message(
                            id=str(uuid.uuid4()),
                            session_id=sid,
                            role="user",
                            content="继续",
                            created_at=datetime.now(timezone.utc)
                            + timedelta(seconds=i),
                        )
                    )
                db.commit()
            self.assertEqual(
                self.client.post(
                    "/api/chat",
                    headers=headers,
                    json={
                        "message": "继续",
                        "workspace_id": "language",
                        "session_id": sid,
                        "ui_locale": "zh-CN",
                    },
                ).status_code,
                200,
            )
            self.assertEqual(policies[-1]["language"], "en")
            with self.factory() as db:
                profile = db.get(SessionProfile, sid)
                ctx = db.get(LearningContext, sid)
                self.assertEqual(profile.learning_goal, "理解代价")
                self.assertEqual(ctx.notes, "我的原始笔记")
                for mid, content in old:
                    self.assertEqual(db.get(Message, mid).content, content)
                self.assertEqual(
                    snapshot(db, sid, ctx, profile)["language_policy"]["language"], "en"
                )
