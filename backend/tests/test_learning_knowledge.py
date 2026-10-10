"""Learning acquisition and chat contracts with isolated DB and scripted LLMs.

These verify orchestration/provenance, not model understanding or semantic quality.
"""

import json
import tempfile
import time
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, patch

import test_dashboard  # imports synthetic settings before app modules
from fastapi import HTTPException
from app.core.config import settings
from app.models.knowledge import Chunk, Document, MessageEvidence, Workspace
from app.services.knowledge_service import model_id, retrieve
from app.services.learning_knowledge import acquire
from app.services.rag_service import grounded_answer
from app.services.smart_routing import previous_decision


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        test_dashboard.DashboardTests.setUp(self)
        self.owner, self.headers = self.users[0]
        with self.factory() as db:
            db.add_all(
                [
                    Workspace(id="w", user_id=self.owner, name="Learning"),
                    Workspace(id="private", user_id=self.users[1][0], name="Other"),
                ]
            )
            db.flush()
            for i, workspace in [(1, "w"), (2, "w"), (3, "private")]:
                db.add(
                    Document(
                        id=f"d{i}",
                        workspace_id=workspace,
                        name=f"Part {i}.md",
                        storage_key="unused",
                        size_bytes=100,
                        status="ready",
                        embedding_model=model_id(),
                        chunk_count=1,
                        created_at=datetime.now(timezone.utc) + timedelta(seconds=i),
                    )
                )
            db.flush()
            for i in range(1, 4):
                db.add(
                    Chunk(
                        id=f"c{i}",
                        document_id=f"d{i}",
                        ordinal=0,
                        page=None,
                        content=[
                            "",
                            "PostgreSQL SQL basics: SELECT retrieves rows.",
                            "PostgreSQL indexes help locate rows. Transactions group operations.",
                            "Private unrelated material.",
                        ][i],
                        vector=[1.0, 0.0],
                    )
                )
            db.commit()
        self.enterContext(patch.object(settings, "LLM_BACKEND", "provider"))
        self.enterContext(patch.object(settings, "CITATION_AUDIT_ENABLED", True))
        self.plan = dict(
            task="planning", document_ids=["d2", "d1"], use_documents=True, query=""
        )
        self.planner = self.enterContext(
            patch(
                "app.services.learning_knowledge.provider_completion",
                side_effect=lambda *a, **k: json.dumps(self.plan),
            )
        )

    def acquisition(self, initial=None, previous=None, search=None, deadline=None):
        with self.factory() as db:
            return acquire(
                db,
                self.owner,
                "w",
                "这些资料怎么学？",
                [],
                {"focus": "SQL"},
                previous or {},
                initial or [],
                search or Mock(return_value=[]),
                deadline if deadline is not None else time.perf_counter() + 75,
                "smart",
            )

    def test_planning_uses_real_excerpts_without_vector_similarity(self):
        sources, context = self.acquisition()
        self.assertEqual({s["document_id"] for s in sources}, {"d1", "d2"})
        self.assertEqual(context["task"], "planning")
        prompt = json.loads(self.planner.call_args.args[3])
        self.assertEqual([d["id"] for d in prompt["documents"]], ["d2", "d1"])
        self.assertNotIn("d3", prompt["samples"])
        self.assertLessEqual(sum(len(s["content"]) for s in sources), 12000)

    def test_wrong_initial_results_trigger_one_scoped_rewrite(self):
        self.plan.update(
            task="document_qa", document_ids=["d1"], query="SELECT retrieves rows"
        )
        search = Mock(
            return_value=[
                dict(number=1, chunk_id="c1", document_id="d1", content="SQL basics")
            ]
        )
        initial = [
            dict(number=1, chunk_id="c2", document_id="d2", content="Transactions")
        ]
        result, _ = self.acquisition(initial, search=search)
        search.assert_called_once()
        self.assertEqual(search.call_args.kwargs["document_ids"], ["d1"])
        self.assertEqual([s["document_id"] for s in result], ["d1"])
        self.assertEqual(
            json.loads(self.planner.call_args.args[3])["initial_results"], initial
        )

    def test_saved_document_scope_and_focus_survive_followup(self):
        self.plan.update(task="explanation", document_ids=["d1"])
        _, context = self.acquisition(previous={"documents": ["d1"]})
        prompt = json.loads(self.planner.call_args.args[3])
        self.assertEqual(prompt["saved_document_ids"], ["d1"])
        self.assertEqual(prompt["documents"][0]["id"], "d1")
        self.assertEqual(prompt["learning_context"]["focus"], "SQL")
        self.assertEqual(context["scope"], ["d1"])

    def test_selected_explanation_materials_are_available_without_another_search(self):
        self.plan.update(task="explanation", document_ids=["d1"], use_documents=True, query="")
        search = Mock(return_value=[])
        result, context = self.acquisition(search=search)
        search.assert_not_called()
        self.assertEqual(context["scope"], ["d1"])
        self.assertEqual([s["document_id"] for s in result], ["d1"])
        self.assertIn("SELECT retrieves rows", result[0]["content"])

    def test_general_concept_does_not_need_document_citations(self):
        self.plan.update(
            task="explanation", use_documents=False, document_ids=[], query=""
        )
        sources, context = self.acquisition()
        self.assertEqual(sources, [])
        with patch(
            "app.services.rag_service.provider_completion",
            return_value=json.dumps(
                dict(
                    answer="An index is a lookup structure.",
                    citations=[],
                    basis="general",
                )
            ),
        ) as model:
            answer, cited, basis = grounded_answer(
                None,
                self.owner,
                "Explain an index",
                [],
                sources,
                answer_mode="smart",
                knowledge_context=context,
                evidence_requested=True,
            )
        self.assertEqual((cited, basis), ([], "general"))
        self.assertIn("lookup structure", answer)
        self.assertEqual(model.call_count, 1)

    def test_processing_and_outdated_documents_exist_but_are_not_evidence(self):
        with self.factory() as db:
            db.get(Document, "d1").status = "processing"
            db.get(Document, "d2").embedding_model = "outdated"
            db.commit()
        search = Mock()
        self.plan["query"] = "SQL"
        sources, context = self.acquisition(search=search)
        self.assertEqual(sources, [])
        self.assertEqual(len(context["documents"]), 2)
        self.assertFalse(any(d["searchable"] for d in context["documents"]))
        search.assert_not_called()

    def test_empty_workspace_still_recognizes_task_and_cannot_invent_documents(self):
        self.plan.update(
            task="explanation", use_documents=False, document_ids=[], query=""
        )
        with self.factory() as db:
            db.add(Workspace(id="empty", name="Empty", user_id=self.owner))
            db.commit()
            sources, context = acquire(
                db,
                self.owner,
                "empty",
                "Teach databases",
                [],
                {},
                {},
                [],
                Mock(),
                time.perf_counter() + 75,
                "smart",
            )
        self.planner.assert_called_once()
        self.assertEqual((sources, context["documents"]), ([], []))

    def test_scope_cannot_cross_user_or_workspace(self):
        self.plan["document_ids"] = ["d3"]
        with self.assertRaises(HTTPException) as error:
            self.acquisition()
        self.assertEqual(error.exception.status_code, 502)
        with self.factory() as db, self.assertRaises(HTTPException) as error:
            acquire(
                db,
                self.users[1][0],
                "w",
                "Teach",
                [],
                {},
                {},
                [],
                Mock(),
                time.perf_counter() + 75,
                "smart",
            )
        self.assertEqual(error.exception.status_code, 404)

    def test_retrieval_filters_explicit_ids_in_actual_sql_pipeline(self):
        with (
            self.factory() as db,
            patch(
                "app.services.knowledge_service.run_worker",
                return_value={"vectors": [[1.0, 0.0]]},
            ),
        ):
            sources = retrieve(db, "w", "SQL", document_ids=["d1", "d3"])
            self.assertEqual([s["document_id"] for s in sources], ["d1"])
            self.assertEqual(retrieve(db, "w", "SQL", document_ids=[]), [])

    def test_exhausted_budget_does_not_call_planner(self):
        with self.assertRaises(HTTPException):
            self.acquisition(deadline=time.perf_counter())
        self.planner.assert_not_called()

    def send(self, message, **extra):
        return self.client.post(
            "/api/chat",
            headers=self.headers,
            json=dict(
                workspace_id="w", session_type="learning", message=message, **extra
            ),
        )

    def generated(self, db, user, system, raw, **kwargs):
        data = json.loads(raw)
        if kwargs.get("metric_phase") == "citation_audit":
            return json.dumps(
                dict(
                    model_knowledge_safe=True,
                    claims=[
                        dict(
                            index=c["index"],
                            support="full",
                            reason="supported",
                            relevant_citations=[s["number"] for s in c["sources"]],
                        )
                        for c in data["claims"]
                    ],
                )
            )
        sources = data["evidence"]
        if sources:
            s = sources[0]
            text = f"{s['content']} [{s['number']}]"
            strict = data.get("knowledge_context", {}).get("task") == "document_qa"
            return json.dumps(
                dict(
                    answer=text,
                    citations=[s["number"]],
                    basis="grounded" if strict else "hybrid",
                    **(dict(model_knowledge=None) if strict else dict(lesson=dict(mode="continue",
                        explanation="建议先练习基础查询，再研究索引，最后练习事务。",
                        next_action="先写一条 SELECT 查询，观察结果。"))),
                    claims=[
                        dict(
                            text=text,
                            citations=[s["number"]],
                            quotes=[dict(citation=s["number"], text=s["content"])],
                        )
                    ],
                    learning=dict(
                        initial_goal="Learn databases",
                        focus="SQL",
                        next_step="",
                        evidence=None,
                    ),
                )
            )
        return json.dumps(
            dict(
                answer="",
                citations=[],
                basis="general",
                lesson=dict(mode="continue", explanation="可以先练习 SQL 基础查询。",
                    next_action="先写一条 SELECT 查询，观察结果。"),
                claims=[],
                learning=dict(
                    initial_goal="Learn databases",
                    focus="SQL",
                    next_step="",
                    evidence=None,
                ),
            )
        )

    def test_chat_learning_plan_and_saved_scope_end_to_end(self):
        with (
            patch(
                "app.services.smart_routing.provider_completion",
                return_value='{"intent":"workspace"}',
            ),
            patch("app.api.routes.chat.retrieve", return_value=[]),
            patch(
                "app.services.rag_service.provider_completion",
                side_effect=self.generated,
            ),
        ):
            first = self.send("我想学习 PostgreSQL，这些资料怎么学？")
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(first.json()["answer_basis"], "hybrid")
            self.assertIn("建议先练习", first.json()["answer"])
            self.assertTrue(first.json()["sources"])
            self.assertNotIn("无法确认", first.json()["answer"])
            sid = first.json()["session_id"]
            self.assertEqual(
                self.send("继续学习上一节", session_id=sid).status_code, 200
            )
        prompt = json.loads(self.planner.call_args.args[3])
        self.assertEqual(prompt["saved_document_ids"], ["d2", "d1"])
        with self.factory() as db:
            mode = db.query(MessageEvidence).first().ai_mode
            self.assertEqual(previous_decision(mode)["documents"], ["d2", "d1"])

    def test_document_qa_rejects_general_substitution(self):
        self.plan.update(task="document_qa", document_ids=["d1"], query="SQL")
        source = dict(
            number=1, chunk_id="c1", document_id="d1", content="SELECT retrieves rows."
        )
        with (
            patch(
                "app.services.smart_routing.provider_completion",
                return_value='{"intent":"workspace"}',
            ),
            patch("app.api.routes.chat.retrieve", return_value=[source]),
            patch(
                "app.services.rag_service.provider_completion",
                return_value=json.dumps(
                    dict(
                        answer="Invented document finding",
                        citations=[],
                        basis="general",
                    )
                ),
            ),
        ):
            result = self.send("第二份文档的具体内容是什么？")
        self.assertEqual(result.status_code, 502)
        with self.factory() as db:
            self.assertEqual(db.query(MessageEvidence).count(), 0)

    def test_learning_plan_then_step_by_step_request_keeps_teaching_and_scope(self):
        with (
            patch(
                "app.services.smart_routing.provider_completion",
                return_value='{"intent":"workspace"}',
            ),
            patch("app.api.routes.chat.retrieve", return_value=[]) as search,
            patch(
                "app.services.rag_service.provider_completion",
                side_effect=self.generated,
            ),
        ):
            first = self.send(
                "我想学习 PostgreSQL，然后我找到了这些资料。我想知道怎么学习这些知识。"
            )
            self.assertEqual(first.status_code, 200, first.text)
            self.plan.update(
                task="explanation", use_documents=False, document_ids=[], query=""
            )
            second = self.send(
                "你可以带我一步步学吗", session_id=first.json()["session_id"]
            )
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["answer_basis"], "general")
        self.assertIn("SQL 基础查询", second.json()["answer"])
        self.assertNotIn("无法确认", second.json()["answer"])
        prompt = json.loads(self.planner.call_args.args[3])
        self.assertEqual(prompt["saved_document_ids"], ["d2", "d1"])
        self.assertEqual(prompt["learning_context"]["focus"], "SQL")
        self.assertTrue(
            any("我想学习 PostgreSQL" in m["content"] for m in prompt["history"])
        )
        search.assert_not_called()
        with self.factory() as db:
            records = db.query(MessageEvidence).all()
            self.assertTrue(
                all(
                    previous_decision(record.ai_mode)["documents"] == ["d2", "d1"]
                    for record in records
                )
            )

    def test_mock_upload_then_plan_uses_indexed_content(self):
        temp_root = Path(__file__).resolve().parents[2] / "data" / "test-runs"
        temp_root.mkdir(parents=True, exist_ok=True)
        with (
            tempfile.TemporaryDirectory(dir=temp_root) as root,
            patch.object(settings, "LOCAL_DATA_DIR", root),
            patch(
                "app.api.routes.knowledge.run_worker",
                return_value={
                    "chunks": [
                        dict(
                            page=None,
                            content="Joins combine rows from tables.",
                            vector=[1.0, 0.0],
                        )
                    ]
                },
            ),
        ):
            upload = self.client.post(
                "/api/workspaces/w/documents",
                headers=self.headers,
                files={"file": ("joins.md", b"Joins combine rows from tables.")},
            )
        self.assertEqual(upload.status_code, 200, upload.text)
        self.assertEqual(upload.json()["status"], "ready")
        self.plan["document_ids"] = [upload.json()["id"]]
        sources, _ = self.acquisition()
        self.assertEqual(sources[0]["content"], "Joins combine rows from tables.")

    def test_pending_index_chat_retains_metadata_and_offers_general_learning(self):
        with self.factory() as db:
            db.get(Document, "d1").status = "processing"
            db.get(Document, "d2").embedding_model = "outdated"
            db.commit()
        with (
            patch(
                "app.services.smart_routing.provider_completion",
                return_value='{"intent":"workspace"}',
            ),
            patch(
                "app.api.routes.chat.retrieve",
                side_effect=HTTPException(409, "Index pending"),
            ),
            patch(
                "app.services.rag_service.provider_completion",
                side_effect=self.generated,
            ),
        ):
            response = self.send("这些资料应该怎么学？")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["sources"], [])
        self.assertIn("正在建立索引", response.json()["answer"])
        self.assertIn("需要重新索引", response.json()["answer"])
        self.assertNotIn("不存在", response.json()["answer"])

    def test_second_search_failure_remains_distinct_and_atomic(self):
        self.plan.update(task="document_qa", query="SQL basics")
        with (
            patch(
                "app.services.smart_routing.provider_completion",
                return_value='{"intent":"general"}',
            ),
            patch(
                "app.api.routes.chat.retrieve",
                side_effect=HTTPException(408, "Timed out"),
            ),
        ):
            response = self.send("文档中的具体规定是什么？")
        self.assertEqual(response.status_code, 503)
        with self.factory() as db:
            self.assertEqual(db.query(MessageEvidence).count(), 0)

    def test_second_document_question_has_verified_scoped_source(self):
        self.plan.update(
            task="document_qa", document_ids=["d1"], query="SELECT retrieves rows"
        )
        source = dict(
            number=1,
            chunk_id="c1",
            document_id="d1",
            document_name="Part 1.md",
            page=None,
            content="PostgreSQL SQL basics: SELECT retrieves rows.",
        )
        with (
            patch(
                "app.services.smart_routing.provider_completion",
                return_value='{"intent":"workspace"}',
            ),
            patch("app.api.routes.chat.retrieve", return_value=[source]) as search,
            patch(
                "app.services.rag_service.provider_completion",
                side_effect=self.generated,
            ),
        ):
            response = self.send("第二份文档的 SELECT 讲了什么？")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["answer_basis"], "grounded")
        self.assertEqual(response.json()["sources"][0]["document_id"], "d1")
        self.assertIn("[1]", response.json()["answer"])
        self.assertEqual(search.call_count, 1)
        self.assertEqual(search.call_args.kwargs["document_ids"], ["d1"])

    def test_no_relevant_results_document_qa_abstains_without_fabrication(self):
        self.plan.update(
            task="document_qa", document_ids=["d1"], query="Missing detail"
        )
        with (
            patch(
                "app.services.smart_routing.provider_completion",
                return_value='{"intent":"workspace"}',
            ),
            patch("app.api.routes.chat.retrieve", return_value=[]),
            patch("app.services.rag_service.provider_completion") as model,
        ):
            response = self.send("第二份文档规定的缺失细节是什么？")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["answer_basis"], "insufficient")
        self.assertEqual(response.json()["sources"], [])
        model.assert_not_called()

    def test_general_teaching_skips_retrieval_even_when_documents_exist(self):
        self.plan.update(
            task="explanation", use_documents=False, document_ids=[], query=""
        )
        with (
            patch(
                "app.api.routes.chat.retrieve",
                side_effect=HTTPException(503, "Broken index"),
            ) as search,
            patch(
                "app.services.rag_service.provider_completion",
                side_effect=self.generated,
            ),
            patch("app.api.routes.chat.decide") as old_router,
        ):
            response = self.send("解释一下数据库索引")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["answer_basis"], "general")
        search.assert_not_called()
        old_router.assert_not_called()

    def test_optional_search_failure_does_not_block_learning_plan(self):
        self.plan.update(task="planning", query="SQL basics")
        with (
            patch(
                "app.api.routes.chat.retrieve",
                side_effect=HTTPException(408, "Timed out"),
            ),
            patch(
                "app.services.rag_service.provider_completion",
                side_effect=self.generated,
            ),
        ):
            response = self.send("帮助我安排这些知识的学习顺序")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("建议先练习", response.json()["answer"])
        self.assertIn("资料搜索暂不可用", response.json()["answer"])
        self.assertNotIn("无法确认", response.json()["answer"])

    def test_empty_workspace_document_question_stays_strict(self):
        self.plan.update(
            task="document_qa", document_ids=[], use_documents=False, query=""
        )
        with self.factory() as db:
            db.add(Workspace(id="empty", name="Empty", user_id=self.owner))
            db.commit()
        with patch("app.services.rag_service.provider_completion") as model:
            response = self.client.post(
                "/api/chat",
                headers=self.headers,
                json=dict(
                    workspace_id="empty",
                    session_type="learning",
                    message="我的文档规定的截止时间是哪天？",
                ),
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["answer_basis"], "insufficient")
        self.assertEqual(response.json()["sources"], [])
        model.assert_not_called()
