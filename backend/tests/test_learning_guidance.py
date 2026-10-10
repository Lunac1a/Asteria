"""Visible guidance and provenance contracts; no paid provider calls."""
import json
import uuid
import unittest
from fastapi import HTTPException
from unittest.mock import patch
import test_dashboard
from test_citation_faithfulness import DATA, generated, verdict
from app.core.config import settings
from app.models.learning import LearningContext
from app.models.knowledge import Workspace
from app.services.learning_service import apply_observation
from app.services.rag_service import grounded_answer


class GuidanceTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(settings, "LLM_BACKEND", "provider"))
        self.enterContext(patch.object(settings, "CITATION_AUDIT_ENABLED", True))
        self.action = "试着预测：转账的第二步失败时，第一步的扣款应该保留还是撤销？"

    def raw(self, action=None):
        invitation = self.action if action is None else action
        return dict(answer="", basis="general", citations=[],
                    lesson=dict(mode="continue" if invitation else "answer_only",
                        explanation="事务将一组操作作为整体提交或回滚。", next_action=invitation or None),
                    learning=dict(focus="事务", evidence=None))

    def call(self, raw, sources=None, **kwargs):
        with patch("app.services.rag_service.provider_completion", return_value=json.dumps(raw)) as provider:
            result = grounded_answer(None, "synthetic", "解释一下事务", [], sources or [],
                answer_mode="smart", learning_context={"focus":"事务"},
                knowledge_context={"task":"explanation"}, **kwargs)
            return result, provider

    def test_explanation_ends_with_same_action_saved_in_notes(self):
        raw = self.raw()
        update = {}
        (answer, sources, basis), provider = self.call(raw, learning_update=update)
        self.assertTrue(answer.endswith(self.action))
        self.assertIn(raw["lesson"]["explanation"], answer)
        self.assertEqual((sources, basis), ([], "general"))
        self.assertEqual(update["value"]["next_step"], self.action)
        self.assertEqual(provider.call_count, 1)
        self.assertIn("Write the PRIMARY response ONCE", provider.call_args.args[2])
        schema = provider.call_args.kwargs["schema"]
        self.assertNotIn("next_step", schema["properties"]["learning"]["anyOf"][1]["properties"])
        self.assertNotIn("model_knowledge", schema["properties"])

    def test_already_present_action_is_not_repeated(self):
        raw = self.raw()
        (answer, _, _), _ = self.call(raw)
        self.assertEqual(answer.count(self.action), 1)

    def test_paraphrased_metadata_never_adds_a_second_question(self):
        raw = self.raw()
        raw["learning"]["next_step"] = "第二步失败后，请判断是提交还是回滚。"
        update = {}
        (answer, _, _), _ = self.call(raw, learning_update=update)
        self.assertEqual(answer.count(self.action), 1)
        self.assertNotIn(raw["learning"]["next_step"], answer)
        self.assertEqual(update["value"]["next_step"], self.action)

    def test_continue_without_visible_action_is_rejected_not_appended(self):
        raw = self.raw()
        raw["lesson"]["next_action"] = None
        raw["learning"]["next_step"] = self.action
        with self.assertRaises(HTTPException) as error:
            self.call(raw)
        self.assertEqual(error.exception.generation_category, "schema_validation")

    def test_opt_out_clears_previous_suggestion_without_inventing_another(self):
        raw = self.raw("")
        (answer, _, _), _ = self.call(raw)
        self.assertEqual(answer, raw["lesson"]["explanation"])
        ctx = LearningContext(session_id="synthetic", next_step="旧练习", focus="事务", evidence=[])
        update = {}
        self.call(raw, learning_update=update)
        apply_observation(ctx, update["value"], "不要提问，只要解释", "user-message")
        self.assertEqual(ctx.next_step, "")
        self.assertEqual(ctx.evidence, [])

    def test_questioning_chat_does_not_get_metadata_appended(self):
        raw = self.raw()
        raw["answer"] = "事务将一组操作作为整体提交或回滚。"
        with patch("app.services.rag_service.provider_completion", return_value=json.dumps(raw)):
            answer, _, _ = grounded_answer(None, "synthetic", "解释事务", [], [], answer_mode="smart",
                knowledge_context={"task":"explanation"})
        self.assertNotIn(self.action, answer)

    def test_hybrid_guidance_is_audited_and_follows_document_context(self):
        case = DATA[1]
        raw = json.loads(generated(case))
        raw.update(basis="hybrid", lesson=dict(mode="continue",
            explanation="先用一个小例子比较搜索策略。", next_action=self.action))
        update = {}
        with patch("app.services.rag_service.provider_completion", side_effect=[json.dumps(raw), verdict(case)]) as provider:
            answer, sources, basis = grounded_answer(None, "synthetic", case["question"], [], case["sources"],
                answer_mode="smart", learning_context={"focus":"Search"},
                knowledge_context={"task":"explanation"}, learning_update=update)
        audit = json.loads(provider.call_args.args[3])
        self.assertIn(self.action, audit["model_knowledge"])
        self.assertTrue(answer.endswith(self.action))
        self.assertEqual(answer.count(self.action), 1)
        self.assertEqual(basis, "hybrid")
        self.assertTrue(sources)
        self.assertEqual(update["value"]["next_step"], self.action)

    def test_failed_knowledge_safety_does_not_leak_guidance(self):
        case = DATA[1]
        raw = json.loads(generated(case))
        raw.update(basis="hybrid", lesson=dict(mode="continue",
            explanation="The document says there is an exam on Friday.",
            next_action="Prepare for the document's Friday exam."))
        audit = json.loads(verdict(case))
        audit["model_knowledge_safe"] = False
        update = {}
        with patch("app.services.rag_service.provider_completion", side_effect=[json.dumps(raw), json.dumps(audit)]):
            answer, _, basis = grounded_answer(None, "synthetic", case["question"], [], case["sources"],
                answer_mode="smart", learning_context={}, knowledge_context={"task":"explanation"}, learning_update=update)
        self.assertNotIn("Friday", answer)
        self.assertEqual(basis, "grounded")
        self.assertFalse((update["value"] or {}).get("next_step"))

    def test_duplicated_action_or_opt_out_with_action_are_rejected(self):
        raw = self.raw()
        raw["lesson"]["explanation"] += "\n\n" + self.action
        with self.assertRaises(HTTPException):
            self.call(raw)
        raw = self.raw()
        raw["lesson"]["mode"] = "pause"
        with self.assertRaises(HTTPException):
            self.call(raw)

    def test_action_can_be_operation_instead_of_question(self):
        action = "在两个连接中分别开启事务，先只观察第一条 SELECT 的结果。"
        update = {}
        (answer, _, _), _ = self.call(self.raw(action), learning_update=update)
        self.assertEqual(answer.count(action), 1)
        self.assertEqual(update["value"]["next_step"], action)

    def test_bad_evidence_cannot_preserve_a_stale_note(self):
        ctx = LearningContext(session_id="synthetic", next_step="旧练习", evidence=[])
        apply_observation(ctx, dict(focus="新主题", next_step=self.action,
            evidence=dict(kind="attempt", text="Invented", quote="not in message")), "用户当前问题", "u")
        self.assertEqual(ctx.next_step, self.action)
        self.assertTrue(ctx.update_warning)
        self.assertEqual(ctx.evidence, [])
        ctx.next_step = "旧操作"
        apply_observation(ctx, dict(focus=["invalid focus"], next_step=self.action, evidence=None), "当前问题", "u")
        self.assertEqual(ctx.next_step, self.action)
        self.assertEqual(ctx.focus, "新主题")
        self.assertTrue(ctx.update_warning)
        apply_observation(ctx, None, "用户当前问题", "u")
        self.assertEqual(ctx.next_step, "")

    def test_partial_document_failure_keeps_audited_action_and_notes(self):
        case = DATA[1]
        raw = json.loads(generated(case))
        raw.update(basis="hybrid", lesson=dict(mode="continue",
            explanation="用一般知识比较搜索策略。", next_action=self.action))
        update = {}
        with patch("app.services.rag_service.provider_completion", side_effect=[json.dumps(raw), verdict(case, support="none", ids=[])]):
            answer, sources, basis = grounded_answer(None, "synthetic", case["question"], [], case["sources"],
                answer_mode="smart", learning_context={}, knowledge_context={"task":"explanation"}, learning_update=update)
        self.assertEqual(basis, "general")
        self.assertEqual(sources, [])
        self.assertEqual(answer.count(self.action), 1)
        self.assertEqual(update["value"]["next_step"], self.action)

    def test_general_label_cannot_bypass_document_attribution_safety(self):
        raw = self.raw()
        raw["lesson"]["explanation"] = "The uploaded manual says the exam is on Friday."
        update = {}
        with patch("app.services.rag_service.provider_completion", side_effect=[json.dumps(raw),
                json.dumps(dict(claims=[], model_knowledge_safe=False))]) as provider:
            answer, sources, basis = grounded_answer(None, "synthetic", "继续学习", [], DATA[1]["sources"],
                answer_mode="smart", learning_context={}, knowledge_context={"task":"explanation"}, learning_update=update)
        self.assertEqual(provider.call_count, 2)
        audit = json.loads(provider.call_args.args[3])
        self.assertEqual(audit["claims"], [])
        self.assertIn(self.action, audit["model_knowledge"])
        self.assertNotIn("Friday", answer)
        self.assertNotIn(self.action, answer)
        self.assertEqual((sources, basis), ([], "insufficient"))
        self.assertIsNone(update["value"])

    def test_general_lesson_with_material_context_can_pass_safety_and_sync(self):
        raw = self.raw()
        update = {}
        with patch("app.services.rag_service.provider_completion", side_effect=[json.dumps(raw),
                json.dumps(dict(claims=[], model_knowledge_safe=True))]):
            answer, sources, basis = grounded_answer(None, "synthetic", "解释事务", [], DATA[1]["sources"],
                answer_mode="smart", learning_context={}, knowledge_context={"task":"explanation"}, learning_update=update)
        self.assertEqual((sources, basis), ([], "general"))
        self.assertEqual(answer.count(self.action), 1)
        self.assertEqual(update["value"]["next_step"], self.action)


class GuidanceAPITests(unittest.TestCase):
    def test_answer_notes_and_opt_out_share_one_action(self):
        test_dashboard.DashboardTests.setUp(self)
        self.enterContext(patch.object(settings, "LLM_BACKEND", "provider"))
        self.enterContext(patch("app.services.learning_knowledge.provider_completion", return_value=json.dumps(
            dict(task="explanation", document_ids=[], use_documents=False, query=""))))
        owner, headers = self.users[0]
        with self.factory() as db:
            db.add(Workspace(id="guidance", user_id=owner, name="Synthetic"))
            db.commit()
        action = "Predict what should happen if the second transfer operation fails."
        raw = dict(answer="", basis="general", citations=[], lesson=dict(mode="continue",
            explanation="A transaction commits or rolls back its operations together.", next_action=action),
            learning=dict(initial_goal="Understand transactions", focus="Transactions", evidence=None))
        def send(question, sid=None):
            return self.client.post("/api/chat", headers=headers, json=dict(message=question, workspace_id="guidance",
                session_type="learning" if sid is None else None, session_id=sid, request_id=str(uuid.uuid4())))
        with patch("app.services.rag_service.provider_completion", return_value=json.dumps(raw)):
            response = send("Explain transactions")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        sid = body["session_id"]
        self.assertTrue(body["answer"].endswith(action))
        state = self.client.get(f"/api/chat/sessions/{sid}/learning", headers=headers).json()
        self.assertEqual(state["next_step"], action)
        raw["lesson"]["mode"] = "answer_only"
        raw["lesson"]["next_action"] = None
        with patch("app.services.rag_service.provider_completion", return_value=json.dumps(raw)):
            response = send("Only explain; no practice question please", sid)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(action, response.json()["answer"])
        state = self.client.get(f"/api/chat/sessions/{sid}/learning", headers=headers).json()
        self.assertEqual(state["next_step"], "")
