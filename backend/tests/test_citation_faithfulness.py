"""Synthetic contracts, not semantic model scores. Provider responses are test doubles, not independent human labels."""

import copy
import json
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
import test_dashboard
from app.core.config import settings
from app.services.citation_service import check_mapping, checked_verdict
from app.services.generation_protocol import GenerationError, failure
from app.services.rag_service import grounded_answer
from app.models.knowledge import Workspace, Document, MessageEvidence
from app.models.messages import Message
from app.models.chat_sessions import ChatSession
from app.models.learning import LearningContext

DATA = json.loads(
    (Path(__file__).resolve().parent / "fixtures/citation_cases.json").read_text(
        encoding="utf-8"
    )
)


def generated(case):
    return json.dumps(
        dict(
            answer=case["answer"],
            citations=case["citations"],
            basis="grounded",
            model_knowledge=None,
            claims=case["claims"],
            learning={
                "initial_goal": "Understand the document",
                "focus": "Costs",
                "next_step": "",
                "evidence": None,
            },
        )
    )


def verdict(case, support="full", ids=None):
    return json.dumps(
        {
            "claims": [
                {
                    "index": 0,
                    "support": support,
                    "relevant_citations": case["citations"] if ids is None else ids,
                    "reason": "supported" if support == "full" else "missing_detail",
                }
            ],
            "model_knowledge_safe": True,
        }
    )


class CitationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(settings, "LLM_BACKEND", "provider"))
        self.enterContext(patch.object(settings, "CITATION_AUDIT_ENABLED", True))

    def call(self, case, **kw):
        return grounded_answer(
            None,
            "synthetic",
            case["question"],
            [],
            case["sources"],
            answer_mode="hybrid",
            evidence_requested=True,
            **kw,
        )

    def test_original_gap_passes_mapping_but_is_withheld_after_partial_verdict(self):
        case = DATA[0]
        original = copy.deepcopy(case)
        self.assertIsNone(
            check_mapping(
                case["answer"], case["citations"], case["claims"], case["sources"]
            )
        )
        with patch(
            "app.services.rag_service.provider_completion",
            side_effect=[generated(case), verdict(case, "partial")],
        ) as call:
            answer, sources, basis = self.call(case)
        self.assertEqual((sources, basis), ([], "insufficient"))
        self.assertIn("haven't included", answer)
        self.assertNotIn("lowest f(n)", answer)
        self.assertEqual(case, original)
        self.assertEqual(call.call_count, 2)
        audit = json.loads(call.call_args.args[3])
        self.assertEqual([s["number"] for s in audit["claims"][0]["sources"]], [1])

    def test_verified_answer_keeps_original_citations_and_source_content(self):
        case = DATA[1]
        with patch(
            "app.services.rag_service.provider_completion",
            side_effect=[generated(case), verdict(case)],
        ):
            answer, sources, basis = self.call(case)
        self.assertEqual(answer, case["answer"])
        self.assertEqual(sources, [case["sources"][1]])
        self.assertEqual(basis, "grounded")

    def test_unmapped_claim_and_fabricated_quote_are_not_repaired(self):
        for name in ("unmapped", "quote"):
            case = copy.deepcopy(DATA[1])
            if name == "unmapped":
                case["answer"] += " The exam is Friday."
            else:
                case["claims"][0]["quotes"][0]["text"] = "Invented source text"
            with (
                self.subTest(name=name),
                patch(
                    "app.services.rag_service.provider_completion",
                    return_value=generated(case),
                ) as call,
            ):
                self.assertEqual(self.call(case)[2], "insufficient")
                self.assertEqual(call.call_count, 1)

    def test_general_has_no_mapping_prompt_or_extra_call(self):
        raw = '{"answer":"A general explanation.","citations":[],"basis":"general"}'
        with patch(
            "app.services.rag_service.provider_completion", return_value=raw
        ) as call:
            answer, sources, basis = grounded_answer(
                None, "synthetic", "General concept", [], [], answer_mode="hybrid"
            )
        self.assertEqual(basis, "general")
        self.assertEqual(call.call_count, 1)
        self.assertNotIn("claims", call.call_args.kwargs["schema"]["properties"])
        self.assertNotIn("Joining these texts", call.call_args.args[2])

    def test_core_invalid_schema_is_rejected_before_audit(self):
        case = DATA[1]
        raw = json.loads(generated(case))
        raw.update(basis="hybrid", model_knowledge=99)
        with patch(
            "app.services.rag_service.provider_completion", return_value=json.dumps(raw)
        ) as call:
            with self.assertRaises(HTTPException):
                self.call(case)
        self.assertEqual(call.call_count, 1)

    def test_expired_budget_does_not_start_another_request(self):
        case = DATA[1]
        diagnostics = {}
        with patch(
            "app.services.rag_service.provider_completion", return_value=generated(case)
        ) as call:
            self.assertEqual(
                self.call(
                    case,
                    evidence_deadline=time.perf_counter(),
                    citation_diagnostics=diagnostics,
                )[2],
                "insufficient",
            )
        self.assertEqual(call.call_count, 1)
        self.assertEqual(diagnostics["outcome"], "budget_exhausted")

    def test_provider_failure_and_invalid_audit_are_not_material_insufficiency(self):
        case = DATA[1]
        for second in [
            failure(GenerationError("provider_timeout")),
            '{"claims":[]}',
            failure(GenerationError("output_truncated")),
        ]:
            with (
                self.subTest(second=second),
                patch(
                    "app.services.rag_service.provider_completion",
                    side_effect=[generated(case), second],
                ) as call,
            ):
                with self.assertRaises(HTTPException):
                    self.call(case)
                self.assertEqual(call.call_count, 2)

    def test_invalid_verdict_indices_ids_and_types_fail_closed(self):
        case = DATA[1]
        for field, value in [
            ("index", True),
            ("index", 1),
            ("relevant_citations", [99]),
            ("support", "unknown"),
        ]:
            raw = json.loads(verdict(case))
            raw["claims"][0][field] = value
            with self.subTest(field=field), self.assertRaises(GenerationError):
                checked_verdict(json.dumps(raw), case["claims"])

    def test_hybrid_general_leak_is_withheld(self):
        case = DATA[1]
        raw = json.loads(generated(case))
        raw.update(
            basis="hybrid", model_knowledge="This course gives a 50 percent grade."
        )
        audit = json.loads(verdict(case))
        audit["model_knowledge_safe"] = False
        with patch(
            "app.services.rag_service.provider_completion",
            side_effect=[json.dumps(raw), json.dumps(audit)],
        ):
            answer, sources, basis = self.call(case)
        self.assertNotIn("50 percent", answer)
        self.assertEqual((sources, basis), ([], "insufficient"))


class CitationAPITests(unittest.TestCase):
    def setUp(self):
        test_dashboard.DashboardTests.setUp(self)
        self.enterContext(patch.object(settings, "LLM_BACKEND", "provider"))
        self.enterContext(patch.object(settings, "CITATION_AUDIT_ENABLED", True))
        self.enterContext(
            patch(
                "app.services.smart_routing.provider_completion",
                return_value='{"intent":"workspace"}',
            )
        )
        self.case = copy.deepcopy(DATA[1])
        for source in self.case["sources"]:
            source["document_id"] = "doc"
        with self.factory() as db:
            db.add(
                Workspace(
                    id="c2", name="Synthetic citation test", user_id=self.users[0][0]
                )
            )
            db.flush()
            db.add(
                Document(
                    id="doc",
                    workspace_id="c2",
                    name="Synthetic",
                    storage_key="unused",
                    size_bytes=1,
                    status="ready",
                    embedding_model="unused",
                )
            )
            db.commit()
        self.enterContext(
            patch("app.api.routes.chat.retrieve", return_value=self.case["sources"])
        )
        self.provider = self.enterContext(
            patch("app.services.rag_service.provider_completion")
        )
        self.request_id = str(uuid.uuid4())

    def send(self, **changes):
        body = dict(
            workspace_id="c2",
            message="Explain from my document.",
            session_type="learning",
            request_id=self.request_id,
        )
        body.update(changes)
        return self.client.post("/api/chat", headers=self.users[0][1], json=body)

    def test_audit_failure_is_atomic_then_same_request_succeeds_once(self):
        self.provider.side_effect = [
            generated(self.case),
            failure(GenerationError("provider_timeout")),
        ]
        self.assertEqual(self.send().status_code, 504)
        with self.factory() as db:
            self.assertEqual(db.query(Message).count(), 0)
            self.assertEqual(db.query(ChatSession).count(), 0)
        self.provider.side_effect = [generated(self.case), verdict(self.case)]
        result = self.send()
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.send().json(), result.json())
        self.assertEqual(self.provider.call_count, 4)
        sid = result.json()["session_id"]
        before = self.client.get(
            f"/api/chat/sessions/{sid}/learning", headers=self.users[0][1]
        ).json()
        self.provider.side_effect = [
            generated(self.case),
            failure(GenerationError("output_truncated")),
        ]
        self.assertEqual(
            self.send(session_id=sid, request_id=str(uuid.uuid4())).status_code, 502
        )
        self.assertEqual(
            self.client.get(
                f"/api/chat/sessions/{sid}/learning", headers=self.users[0][1]
            ).json(),
            before,
        )
        with self.factory() as db:
            self.assertEqual(db.query(Message).count(), 2)

    def test_unconfirmed_answer_does_not_create_false_learning_evidence(self):
        self.provider.side_effect = [
            generated(self.case),
            verdict(self.case, "partial"),
        ]
        response = self.send()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["sources"], [])
        self.assertEqual(response.json()["answer_basis"], "insufficient")
        with self.factory() as db:
            context = db.get(LearningContext, response.json()["session_id"])
            self.assertEqual(context.evidence, [])
            self.assertEqual(context.focus, "")
            self.assertTrue(context.update_warning)
            ev = db.query(MessageEvidence).one()
            self.assertIn("citation_check=support_unconfirmed", ev.ai_mode)
