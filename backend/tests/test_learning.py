"""MVP API regressions. Models are test doubles; no paid provider requests."""

import io
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Reuse the existing synthetic settings bootstrapping, before application imports.
from test_auth import Base, get_db
from app.main import create_app
from app.core.config import settings
from app.core.security import create_access_token, decrypt_text
from app.models.knowledge import Chunk, Document
from app.models.messages import Message
from app.models.user_llm_settings import UserLLMSetting
from app.services.rag_service import grounded_answer
from app.services.knowledge_service import run_worker

from make_test_pdf import make_pdf  # noqa: E402


class LearningAPITests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(__file__).resolve().parents[2] / "data" / "test-runs"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.enterContext(
            patch(
                "app.services.smart_routing.provider_completion",
                return_value='{"intent":"general"}',
            )
        )
        self.temp = tempfile.TemporaryDirectory(dir=temp_root)
        self.addCleanup(self.temp.cleanup)
        for key, value in {
            "LOCAL_DATA_DIR": self.temp.name,
            "EMBEDDING_BACKEND": "test",
            "LLM_BACKEND": "test",
            "RETRIEVAL_MIN_SCORE": 0.15,
        }.items():
            self.enterContext(patch.object(settings, key, value))
        url = os.environ.get("ASTERIA_TEST_DATABASE_URL")
        self.schema = "test_" + uuid.uuid4().hex
        if url:
            parsed = make_url(url)
            if parsed.host not in {
                "localhost",
                "127.0.0.1",
            } or not parsed.database.endswith("_test"):
                raise RuntimeError("Only a local *_test database may be used")
            self.admin = create_engine(url)
            with self.admin.begin() as connection:
                connection.execute(text(f"CREATE SCHEMA {self.schema}"))
            self.engine = create_engine(
                url, connect_args={"options": f"-csearch_path={self.schema}"}
            )
        else:
            self.engine = create_engine(
                "sqlite://",
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine)
        self.addCleanup(self.cleanup_db)
        app = create_app()

        def isolated_db():
            with self.factory() as db:
                yield db

        app.dependency_overrides[get_db] = isolated_db
        self.client = self.enterContext(TestClient(app))
        self.a = self.user("learner-a@example.com")
        self.b = self.user("learner-b@example.com")
        self.workspace = self.create_workspace(self.a, "Independent course")
        self.other_workspace = self.create_workspace(self.a, "Other course")
        self.pdf = make_pdf(
            Path(self.temp.name) / "independent-course.pdf"
        ).read_bytes()

    def cleanup_db(self):
        self.engine.dispose()
        if hasattr(self, "admin"):
            with self.admin.begin() as connection:
                connection.execute(text(f"DROP SCHEMA {self.schema} CASCADE"))
            self.admin.dispose()

    def user(self, email):
        payload = {"email": email, "password": "Independent-Test-42"}
        self.assertEqual(
            self.client.post("/api/register", json=payload).status_code, 200
        )
        token = self.client.post("/api/login", json=payload).json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    def create_workspace(self, headers, name):
        response = self.client.post(
            "/api/workspaces", json={"name": name}, headers=headers
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["id"]

    def upload(self, content=None, name="independent-course.pdf"):
        response = self.client.post(
            f"/api/workspaces/{self.workspace}/documents",
            files={"file": (name, content if content is not None else self.pdf)},
            headers=self.a,
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def ask(self, question="What is the final project worth?", **kwargs):
        return self.client.post(
            "/api/chat",
            json={"workspace_id": self.workspace, "message": question, **kwargs},
            headers=self.a,
        )

    def test_full_pdf_chat_restore_continue_and_idempotency(self):
        doc = self.upload()
        self.assertEqual((doc["status"], doc["chunk_count"]), ("ready", 2))
        request_id = str(uuid.uuid4())
        first = self.ask(request_id=request_id)
        self.assertEqual(first.status_code, 200)
        result = first.json()
        self.assertIn("35 percent", result["answer"])
        self.assertEqual(result["sources"][0]["page"], 2)
        self.assertEqual(
            result["sources"][0]["document_name"], "independent-course.pdf"
        )
        self.assertEqual(self.ask(request_id=request_id).json(), result)
        self.assertEqual(
            self.ask("Different question", request_id=request_id).status_code, 409
        )
        sessions = self.client.get(
            "/api/chat/sessions",
            params={"workspace_id": self.workspace},
            headers=self.a,
        ).json()
        self.assertEqual(len(sessions), 1)
        messages = self.client.get(
            f"/api/chat/sessions/{result['session_id']}/messages", headers=self.a
        ).json()
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])
        self.assertEqual(messages[1]["sources"], result["sources"])
        second = self.ask(
            "What is the report word count?", session_id=result["session_id"]
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["session_id"], result["session_id"])
        self.assertIn("1200", second.json()["answer"])

    def test_user_workspace_session_file_and_source_isolation(self):
        doc = self.upload()
        answer = self.ask().json()
        for path in [
            f"/api/workspaces/{self.workspace}/documents",
            f"/api/workspaces/{self.workspace}/documents/{doc['id']}/file",
            f"/api/chat/sessions/{answer['session_id']}/messages",
        ]:
            self.assertEqual(self.client.get(path, headers=self.b).status_code, 404)
        for action in ["reindex", ""]:
            path = f"/api/workspaces/{self.workspace}/documents/{doc['id']}" + (
                "/reindex" if action else ""
            )
            response = (
                self.client.post(path, headers=self.b)
                if action
                else self.client.delete(path, headers=self.b)
            )
            self.assertEqual(response.status_code, 404)
        response = self.client.post(
            "/api/chat",
            json={"workspace_id": self.workspace, "message": "Secret?"},
            headers=self.b,
        )
        self.assertEqual(response.status_code, 404)
        response = self.client.post(
            "/api/chat",
            json={
                "workspace_id": self.other_workspace,
                "session_id": answer["session_id"],
                "message": "Continue",
            },
            headers=self.a,
        )
        self.assertEqual(response.status_code, 404)
        other = self.client.post(
            "/api/chat",
            json={
                "workspace_id": self.other_workspace,
                "message": "What is the project worth?",
            },
            headers=self.a,
        ).json()
        self.assertEqual(other["sources"], [])

    def test_bad_empty_scanned_large_and_non_utf8_uploads(self):
        self.assertEqual(self.upload(b"not a pdf")["status"], "failed")
        self.assertEqual(self.upload(b"\xff\xfe", "bad.txt")["status"], "failed")
        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        buffer = io.BytesIO()
        writer.write(buffer)
        self.assertIn("No readable text", self.upload(buffer.getvalue())["error"])
        response = self.client.post(
            f"/api/workspaces/{self.workspace}/documents",
            files={"file": ("empty.pdf", b"")},
            headers=self.a,
        )
        self.assertEqual(response.status_code, 422)
        response = self.client.post(
            f"/api/workspaces/{self.workspace}/documents",
            files={"file": ("bad.exe", b"123")},
            headers=self.a,
        )
        self.assertEqual(response.status_code, 415)
        with patch.object(settings, "MAX_UPLOAD_BYTES", 5):
            response = self.client.post(
                f"/api/workspaces/{self.workspace}/documents",
                files={"file": ("huge.txt", b"123456")},
                headers=self.a,
            )
            self.assertEqual(response.status_code, 413)

    def test_delete_removes_file_vectors_and_future_retrieval_but_keeps_snapshot(self):
        doc = self.upload()
        answer = self.ask().json()
        with self.factory() as db:
            path = (
                Path(settings.LOCAL_DATA_DIR) / db.get(Document, doc["id"]).storage_key
            )
        response = self.client.delete(
            f"/api/workspaces/{self.workspace}/documents/{doc['id']}", headers=self.a
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(path.exists())
        with self.factory() as db:
            self.assertEqual(db.query(Chunk).count(), 0)
        self.assertEqual(self.ask().json()["sources"], [])
        history = self.client.get(
            f"/api/chat/sessions/{answer['session_id']}/messages", headers=self.a
        ).json()
        self.assertEqual(history[1]["sources"], answer["sources"])

    def test_provider_failure_is_atomic_and_retry_recovers(self):
        self.upload()
        with patch(
            "app.api.routes.chat.grounded_answer",
            side_effect=__import__("fastapi").HTTPException(
                502, "Test provider unavailable"
            ),
        ):
            self.assertEqual(self.ask().status_code, 502)
        with self.factory() as db:
            self.assertEqual(db.query(Message).count(), 0)
        self.assertEqual(self.ask().status_code, 200)

    def test_unsupported_abstains_but_invalid_format_and_citations_fail(self):
        doc = self.upload()
        response = self.client.post(
            "/api/settings/llm",
            json={
                "api_key": "test-secret",
                "model_name": "test-model",
                "base_url": "https://integrate.api.nvidia.com/v1",
            },
            headers=self.a,
        )
        self.assertEqual(response.status_code, 200)
        sources = [{"number": 1, "document_id": doc["id"], "content": "Actual source"}]
        bad_results = [
            '{"answer":"Made up [99]","citations":[99],"supported":true}',
            '{"answer":"Made up [2]","citations":[1],"supported":true}',
            '{"answer":"No evidence","citations":[],"supported":false}',
            "not JSON",
        ]
        with self.factory() as db, patch.object(settings, "LLM_BACKEND", "provider"):
            user_id = db.query(UserLLMSetting).one().user_id
            for result in bad_results:
                with patch(
                    "app.services.rag_service.generate_response", return_value=result
                ):
                    if '"supported":false' in result:
                        self.assertEqual(
                            grounded_answer(db, user_id, "Question", [], sources),
                            (
                                "The available workspace materials do not support an answer. Please add relevant materials or ask a more specific question.",
                                [],
                                "insufficient",
                            ),
                        )
                    else:
                        with self.assertRaises(
                            __import__("fastapi").HTTPException
                        ) as exc:
                            grounded_answer(db, user_id, "Question", [], sources)
                        self.assertEqual(exc.exception.status_code, 502)

    def test_settings_keeps_key_and_rejects_untrusted_urls(self):
        payload = {
            "api_key": "synthetic-provider-key",
            "model_name": "test-model",
            "base_url": "https://integrate.api.nvidia.com/v1",
        }
        self.assertEqual(
            self.client.post(
                "/api/settings/llm", json=payload, headers=self.a
            ).status_code,
            200,
        )
        del payload["api_key"]
        payload["model_name"] = "new-model"
        self.assertEqual(
            self.client.post(
                "/api/settings/llm", json=payload, headers=self.a
            ).status_code,
            200,
        )
        with self.factory() as db:
            self.assertEqual(
                decrypt_text(db.query(UserLLMSetting).one().encrypted_api_key),
                "synthetic-provider-key",
            )
        for url in [
            "http://127.0.0.1/v1",
            "https://evil.example/v1",
            "https://integrate.api.nvidia.com@evil.example/v1",
        ]:
            self.assertEqual(
                self.client.post(
                    "/api/settings/llm",
                    json={**payload, "base_url": url},
                    headers=self.a,
                ).status_code,
                422,
            )
        self.assertEqual(
            self.client.post(
                "/api/settings/llm", json={**payload, "api_key": ""}, headers=self.a
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.post(
                "/api/settings/llm", json=payload, headers=self.b
            ).status_code,
            400,
        )

        corrected_url = "https://compatible.example/v1"
        with patch.object(
            settings, "LLM_ALLOWED_BASE_URLS", payload["base_url"] + "," + corrected_url
        ):
            response = self.client.post(
                "/api/settings/llm",
                json={**payload, "base_url": corrected_url},
                headers=self.a,
            )
            self.assertEqual(response.status_code, 400)
            self.assertEqual(
                self.client.get("/api/settings/llm", headers=self.a).json()["base_url"],
                payload["base_url"],
            )

    def test_malformed_auth_and_unknown_users_are_rejected(self):
        for data in [
            [],
            {"email": 123, "password": []},
            {"email": "a@example.com", "password": "密" * 30},
        ]:
            self.assertEqual(self.client.post("/api/login", json=data).status_code, 400)
        for sub in ["not-a-uuid", str(uuid.uuid4()), 123]:
            token = create_access_token({"sub": sub})
            self.assertEqual(
                self.client.get(
                    "/api/workspaces", headers={"Authorization": f"Bearer {token}"}
                ).status_code,
                401,
            )
        response = self.client.post(
            "/api/register", json={"email": "utf8@example.com", "password": "密" * 30}
        )
        self.assertEqual(response.status_code, 422)

    def test_index_timeout_records_failed_and_retry(self):
        import subprocess

        with patch(
            "app.services.knowledge_service.subprocess.run",
            side_effect=subprocess.TimeoutExpired("worker", 1),
        ):
            doc = self.upload()
        self.assertEqual(doc["status"], "failed")
        self.assertIn("time limit", doc["error"])
        result = self.client.post(
            f"/api/workspaces/{self.workspace}/documents/{doc['id']}/reindex",
            headers=self.a,
        )
        self.assertEqual(result.json()["status"], "ready")

    def test_worker_busy_does_not_spawn_unbounded_processes(self):
        from app.services.knowledge_service import worker_slot

        worker_slot.acquire()
        try:
            with self.assertRaises(__import__("fastapi").HTTPException) as error:
                run_worker({"texts": ["test"]}, 1)
            self.assertEqual(error.exception.status_code, 429)
        finally:
            worker_slot.release()

    def test_validation_never_echoes_passwords_or_provider_keys(self):
        password = "SensitiveSyntheticPassword" * 4
        response = self.client.post(
            "/api/register", json={"email": "invalid", "password": password}
        )
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(password, response.text)
        key = "synthetic-provider-key-never-echo"
        response = self.client.post(
            "/api/settings/llm",
            json={
                "api_key": key,
                "model_name": "",
                "base_url": "http://private.invalid",
            },
            headers=self.a,
        )
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(key, response.text)

    def test_request_size_limit_runs_before_multipart_processing(self):
        response = self.client.post(
            f"/api/workspaces/{self.workspace}/documents",
            content=b"not multipart",
            headers={
                **self.a,
                "Content-Type": "multipart/form-data; boundary=x",
                "Content-Length": str(11 * 1024 * 1024),
            },
        )
        self.assertEqual(response.status_code, 413)

    def test_self_contained_question_does_not_inherit_previous_retrieval_query(self):
        self.upload()
        first = self.ask().json()
        question = "Who won the 2022 football world cup?"
        with patch("app.api.routes.chat.retrieve", return_value=[]) as retrieve:
            response = self.ask(question, session_id=first["session_id"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["sources"], [])
        retrieve.assert_not_called()

    def test_corrupted_provider_key_returns_safe_error(self):
        self.upload()
        self.client.post(
            "/api/settings/llm",
            json={
                "api_key": "test-key",
                "model_name": "test-model",
                "base_url": "https://integrate.api.nvidia.com/v1",
            },
            headers=self.a,
        )
        with self.factory() as db:
            db.query(
                UserLLMSetting
            ).one().encrypted_api_key = "corrupted-synthetic-ciphertext"
            db.commit()
        with patch.object(settings, "LLM_BACKEND", "provider"):
            response = self.ask()
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("corrupted-synthetic-ciphertext", response.text)

    def test_modes_basis_history_and_idempotency(self):
        doc = self.upload()
        self.client.post(
            "/api/settings/llm",
            json={
                "api_key": "synthetic",
                "model_name": "test",
                "base_url": "https://integrate.api.nvidia.com/v1",
            },
            headers=self.a,
        )
        sources = [
            {
                "number": 1,
                "document_id": doc["id"],
                "content": "Chlorophyll absorbs sunlight",
                "page": 1,
                "document_name": doc["name"],
            }
        ]
        with (
            patch.object(settings, "LLM_BACKEND", "provider"),
            patch("app.api.routes.chat.retrieve", return_value=sources),
        ):
            for basis in ["grounded", "hybrid", "general"]:
                numbers = [] if basis == "general" else [1]
                text = (
                    "Model knowledge (unverified): explanation"
                    if basis == "general"
                    else "Document evidence: sunlight [1]\nModel knowledge (unverified): explanation"
                    if basis == "hybrid"
                    else "Sunlight [1]"
                )
                with patch(
                    "app.services.rag_service.generate_response",
                    return_value=__import__("json").dumps(
                        {
                            "answer": text,
                            "citations": numbers,
                            "basis": basis,
                            "model_knowledge": "Additional general explanation",
                        }
                    ),
                ):
                    result = self.ask(
                        "Explain photosynthesis"
                        if basis == "general"
                        else "Explain photosynthesis from the document"
                    ).json()
                    self.assertEqual(result["answer_basis"], basis)
                    history = self.client.get(
                        f"/api/chat/sessions/{result['session_id']}/messages",
                        headers=self.a,
                    ).json()
                    self.assertEqual(history[-1]["answer_basis"], basis)
                    strict = self.ask("Explain photosynthesis", answer_mode="document")
                    if basis == "grounded":
                        self.assertEqual(strict.json()["answer_basis"], "grounded")
                    else:
                        self.assertEqual(
                            strict.status_code, 502
                        )  # Invalid contract is not evidence absence.
        with patch("app.api.routes.chat.retrieve", return_value=[]):
            self.assertEqual(
                self.ask("课程截止日期是什么？").json()["answer_basis"], "insufficient"
            )
            self.assertEqual(self.ask("解释光合作用").json()["answer_basis"], "general")
            self.assertEqual(
                self.ask("解释光合作用", answer_mode="document").json()["answer_basis"],
                "insufficient",
            )
        request_id = str(uuid.uuid4())
        result = self.ask(
            "Explain photosynthesis", learning_mode="socratic", request_id=request_id
        )
        self.assertEqual(result.json()["learning_mode"], "socratic")
        self.assertEqual(
            self.ask("Explain photosynthesis", request_id=request_id).status_code, 409
        )

    def test_distinct_search_provider_timeout_format_errors_leave_no_messages(self):
        from fastapi import HTTPException

        self.upload()
        self.client.post(
            "/api/settings/llm",
            json={
                "api_key": "synthetic",
                "model_name": "test",
                "base_url": "https://integrate.api.nvidia.com/v1",
            },
            headers=self.a,
        )
        with patch(
            "app.api.routes.chat.retrieve",
            side_effect=HTTPException(408, "worker timeout"),
        ):
            self.assertEqual(self.ask().status_code, 503)
        with patch.object(settings, "LLM_BACKEND", "provider"):
            for failure, code in [
                (RuntimeError("unavailable"), 502),
                (RuntimeError("45-second time limit"), 504),
            ]:
                with patch(
                    "app.services.rag_service.generate_response", side_effect=failure
                ):
                    self.assertEqual(self.ask().status_code, code)
            with patch(
                "app.services.rag_service.generate_response", return_value="not json"
            ):
                self.assertEqual(self.ask().status_code, 502)
        with self.factory() as db:
            self.assertEqual(db.query(Message).count(), 0)

    def test_completed_turn_recovery_is_owner_scoped_and_modes_survive(self):
        request_id = str(uuid.uuid4())
        result = self.ask(
            "Explain a concept", request_id=request_id, learning_mode="socratic"
        ).json()
        response = self.client.get(f"/api/chat/turns/{request_id}", headers=self.a)
        self.assertEqual(response.json(), result)
        self.assertEqual(
            self.client.get(
                f"/api/chat/turns/{request_id}", headers=self.b
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                f"/api/chat/turns/{uuid.uuid4()}", headers=self.a
            ).status_code,
            404,
        )

    def test_outdated_index_is_not_treated_as_no_evidence(self):
        self.upload()
        with patch.object(settings, "EMBEDDING_MODEL", "different-model"):
            response = self.ask("Explain photosynthesis from the document")
        self.assertEqual(response.status_code, 409)
        self.assertIn("reindex", response.text)
        with self.factory() as db:
            self.assertEqual(db.query(Message).count(), 0)

    def test_hybrid_knowledge_cannot_carry_document_citations(self):
        self.upload()
        self.client.post(
            "/api/settings/llm",
            json={
                "api_key": "synthetic",
                "model_name": "test",
                "base_url": "https://integrate.api.nvidia.com/v1",
            },
            headers=self.a,
        )
        for result in [
            '{"answer":"Light [1]", "citations":[1], "basis":"hybrid", "model_knowledge":"Unverified knowledge [1]"}',
            '{"answer":"Light [1]. Extra knowledge", "citations":[1], "basis":"hybrid", "model_knowledge":"Extra knowledge"}',
        ]:
            with (
                self.subTest(result=result),
                patch.object(settings, "LLM_BACKEND", "provider"),
                patch(
                    "app.services.rag_service.generate_response", return_value=result
                ),
            ):
                response = self.ask("Explain photosynthesis")
                self.assertEqual(response.status_code, 502)
        with self.factory() as db:
            self.assertEqual(db.query(Message).count(), 0)

    def test_course_specific_smart_question_never_falls_back_to_model_knowledge(self):
        self.upload()
        self.client.post(
            "/api/settings/llm",
            json={
                "api_key": "synthetic",
                "model_name": "test",
                "base_url": "https://integrate.api.nvidia.com/v1",
            },
            headers=self.a,
        )
        with (
            patch.object(settings, "LLM_BACKEND", "provider"),
            patch(
                "app.services.rag_service.generate_response",
                return_value='{"answer":"Guessed deadline", "citations":[], "basis":"general"}',
            ),
        ):
            response = self.ask("When is the assignment due?")
        self.assertEqual(
            response.status_code, 502
        )  # Unsupported general fallback is invalid.
        with (
            patch("app.api.routes.chat.retrieve", return_value=[]),
            patch("app.services.rag_service.generate_response") as generate,
        ):
            response = self.ask("需要做几分钟的展示？")
        self.assertEqual(response.json()["answer_basis"], "insufficient")
        generate.assert_not_called()
