"""D4-B policy/API contracts. Provider outputs here are explicit doubles, not quality scores."""

import json
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
import test_dashboard
from app.core.config import settings
from app.models.knowledge import Workspace, MessageEvidence
from app.models.messages import Message
from app.services.smart_routing import decide, explicit_evidence
from app.services.rag_service import grounded_answer


class RoutingTests(unittest.TestCase):
    def test_fixed_matrix_contract(self):
        rows = json.loads(
            (Path(__file__).parent / "fixtures/routing_cases.json").read_text(
                encoding="utf-8"
            )
        )["policy"]
        with patch.object(settings, "LLM_BACKEND", "provider"):
            for row in rows:
                with (
                    self.subTest(case=row["id"]),
                    patch(
                        "app.services.smart_routing.provider_completion",
                        return_value=json.dumps({"intent": row["intent"]}),
                    ),
                ):
                    decision = decide(
                        None, None, row["question"], [], row.get("previous")
                    )
                    self.assertEqual(decision["action"], row["action"])

    def test_invalid_route_never_silently_falls_back(self):
        with patch.object(settings, "LLM_BACKEND", "provider"):
            for value in [
                "not JSON",
                "[]",
                '{"intent":"unknown"}',
                '{"intent":"general","answer":"invented"}',
            ]:
                with (
                    patch(
                        "app.services.smart_routing.provider_completion",
                        return_value=value,
                    ),
                    self.assertRaises(HTTPException) as error,
                ):
                    decide(None, None, "Explain atoms", [])
                self.assertEqual(error.exception.status_code, 502)
                self.assertIn("routing format", error.exception.detail)

    def test_insufficient_can_explain_generally_without_citations(self):
        result = {
            "answer": "Unknown",
            "basis": "insufficient",
            "citations": [],
            "model_knowledge": "In general, a penalty is a consequence of a rule violation.",
        }
        with (
            patch.object(settings, "LLM_BACKEND", "provider"),
            patch(
                "app.services.rag_service.provider_completion",
                return_value=json.dumps(result),
            ),
        ):
            answer, sources, basis = grounded_answer(
                None,
                None,
                "What is my course penalty?",
                [],
                [],
                "smart",
                evidence_requested=True,
            )
        self.assertEqual((sources, basis), ([], "insufficient"))
        self.assertIn("cannot confirm", answer)
        self.assertIn("General explanation (not confirmed", answer)

    def test_invalid_answer_is_not_insufficient_and_citation_validation_stays_on(self):
        cases = [
            {"answer": "Guess", "basis": "general", "citations": []},
            {"answer": "Guess [9]", "basis": "grounded", "citations": [9]},
            {
                "answer": "Unknown",
                "basis": "insufficient",
                "citations": [],
                "model_knowledge": "General [1]",
            },
        ]
        for result in cases:
            with (
                patch.object(settings, "LLM_BACKEND", "provider"),
                patch(
                    "app.services.rag_service.provider_completion",
                    return_value=json.dumps(result),
                ),
                self.assertRaises(HTTPException) as error,
            ):
                grounded_answer(
                    None,
                    None,
                    "According to my notes?",
                    [],
                    [],
                    "smart",
                    evidence_requested=True,
                )
            self.assertEqual(error.exception.status_code, 502)


class SmartAPITests(unittest.TestCase):
    def setUp(self):
        test_dashboard.DashboardTests.setUp(self)
        self.headers = self.users[0][1]
        with self.factory() as db:
            db.add(
                Workspace(id="smart", name="Smart contract", user_id=self.users[0][0])
            )
            db.commit()
        self.enterContext(patch.object(settings, "LLM_BACKEND", "provider"))
        self.classifier = self.enterContext(
            patch(
                "app.services.smart_routing.provider_completion",
                return_value='{"intent":"general"}',
            )
        )
        self.search = self.enterContext(
            patch("app.api.routes.chat.retrieve", return_value=[])
        )
        self.answer = self.enterContext(
            patch(
                "app.api.routes.chat.grounded_answer",
                return_value=("A test explanation", [], "general"),
            )
        )

    def send(self, question, session=None, kind="questioning", request=None):
        return self.client.post(
            "/api/chat",
            headers=self.headers,
            json=dict(
                message=question,
                workspace_id="smart",
                session_id=session,
                session_type=kind,
                request_id=request or str(uuid.uuid4()),
            ),
        )

    def test_general_skips_broken_retrieval_for_both_types(self):
        self.search.side_effect = HTTPException(503, "Broken index")
        for kind in ["questioning", "learning"]:
            self.assertEqual(self.send("Explain atoms", kind=kind).status_code, 200)
        self.search.assert_not_called()

    def test_document_followups_persist_anchor_beyond_history_and_topic_switch(self):
        first = self.send("According to my document, explain search.").json()
        sid = first["session_id"]
        for _ in range(7):
            self.assertEqual(self.send("Why?", sid).status_code, 200)
            self.assertEqual(
                self.search.call_args.args[2],
                "According to my document, explain search. Why?",
            )
        self.assertTrue(
            any(
                m["content"] == "According to my document, explain search."
                for m in self.answer.call_args.args[3]
            )
        )
        with self.factory() as db:
            modes = [m.ai_mode for m in db.query(MessageEvidence).all()]
            self.assertTrue(all("route=retrieve" in mode for mode in modes))
        self.search.reset_mock()
        self.send("New topic: explain atoms.", sid)
        self.search.assert_not_called()

    def test_model_elliptical_followup_preserves_evidence_intent(self):
        sid = self.send("According to my document, explain search.").json()[
            "session_id"
        ]
        self.classifier.return_value = '{"intent":"followup"}'
        self.send("And the other search strategy?", sid)
        self.assertIn("According to my document", self.search.call_args.args[2])

    def test_citation_followup_carries_general_topic_into_search(self):
        sid = self.send("Explain atoms").json()["session_id"]
        self.send("Cite a source for your explanation.", sid)
        self.assertEqual(
            self.search.call_args.args[2],
            "Explain atoms Cite a source for your explanation.",
        )

    def test_failures_are_distinct_atomic_and_recover_same_id(self):
        rid = str(uuid.uuid4())
        for failure, fragment in [
            (HTTPException(503, "Material search failed"), "search"),
            (HTTPException(409, "Materials need reindex"), "reindex"),
        ]:
            self.search.side_effect = failure
            response = self.send("According to my document?", request=rid)
            self.assertEqual(response.status_code, failure.status_code)
            self.assertIn(fragment, response.text)
        self.search.side_effect = None
        for detail in [
            "The model provider is unavailable",
            "The model returned an invalid answer format",
        ]:
            self.answer.side_effect = HTTPException(502, detail)
            self.assertEqual(
                self.send("According to my document?", request=rid).status_code, 502
            )
        with self.factory() as db:
            self.assertEqual(db.query(Message).count(), 0)
        self.answer.side_effect = None
        good = self.send("According to my document?", request=rid)
        self.assertEqual(good.status_code, 200)
        self.assertEqual(
            self.send("According to my document?", request=rid).json(), good.json()
        )

    def test_route_failure_is_not_saved_or_retrieved(self):
        self.classifier.return_value = "invalid"
        response = self.send("Explain atoms")
        self.assertEqual(response.status_code, 502)
        self.search.assert_not_called()
        self.answer.assert_not_called()
        with self.factory() as db:
            self.assertEqual(db.query(Message).count(), 0)


class CrossLanguageRoutingTests(unittest.TestCase):
    def test_explicit_requests_and_protected_facts(self):
        rows = json.loads(
            (Path(__file__).parent / "fixtures/routing_cases.json").read_text(
                encoding="utf-8"
            )
        )["cross_language"]
        with patch.object(settings, "LLM_BACKEND", "provider"):
            for row in rows:
                with (
                    self.subTest(case=row["id"]),
                    patch(
                        "app.services.smart_routing.provider_completion",
                        return_value='{"intent":"general"}',
                    ) as classifier,
                ):
                    history = (
                        [
                            {
                                "role": "user",
                                "content": "According to my course document?",
                            }
                        ]
                        if row.get("previous")
                        else []
                    )
                    result = decide(
                        None, None, row["question"], history, row.get("previous")
                    )
                    self.assertEqual(result["action"], row["action"])
                    if row["id"] != "ordinary-concept":
                        classifier.assert_not_called()

    def test_negation_does_not_remove_positive_request_or_course_fact(self):
        for question in [
            "不要使用资料，直接解释 Attention。",
            "Explain A* without using Search.pdf.",
        ]:
            self.assertFalse(explicit_evidence(question))
        for question in [
            "不要使用资料，我的作业截止日期是什么？",
            "不要使用旧文档，根据上传的讲义核对课程规定。",
            "Without using documents, what is my student number?",
            "Use general knowledge, then cite the uploaded notes.",
        ]:
            self.assertTrue(explicit_evidence(question), question)
