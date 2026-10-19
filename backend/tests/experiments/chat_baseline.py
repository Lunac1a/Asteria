"""Sequential real-RAG HTTP benchmark; only external LLM generation is mocked."""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
from pathlib import Path
import platform
import shutil
import socket
import statistics
import subprocess
import tempfile
import threading
import time
import traceback
from unittest.mock import patch
import uuid

import workspace_creation_concurrency as f
from app.api.routes import chat
from app.core.config import settings
from app.core.security import encrypt_text
from app.models.chat_sessions import ChatSession
from app.models.chat_turn import ChatTurn
from app.models.messages import Message
from app.models.session_profile import SessionProfile
from app.models.knowledge import Document, Chunk, SessionWorkspace, MessageEvidence
from app.models.user_llm_settings import UserLLMSetting
from app.services.chat import answers
from app.services.knowledge import retrieval
from app.services.llm import completion
import sqlalchemy

QUESTION = "According to the document, what does a database transaction guarantee?"
CONTENT = "A database transaction guarantees atomicity: either all changes commit, or none of the changes are applied."
ANSWER = "A database transaction guarantees atomicity: either all changes commit, or none are applied. [1]"
HISTORY = [{"role": "user", "content": "I am studying database transactions."},
           {"role": "assistant", "content": "We can examine their guarantees using your document."}]


def distribution(values):
    ordered = sorted(values)
    return {"n": len(values), "mean_ms": statistics.mean(values),
        **{f"p{p}_ms": ordered[math.ceil(len(values) * p / 100) - 1] for p in (50, 95, 99)},
        "min_ms": min(values), "max_ms": max(values)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--pgdata", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    parsed = f.make_url(args.url)
    assert parsed.host == "127.0.0.1" and parsed.port == 55489
    assert parsed.database == "workspace_concurrency_test"
    assert args.pgdata.startswith("/tmp/asteria-concurrency-cross-")
    engine = f.create_engine(args.url, connect_args={"connect_timeout": 5,
        "options": "-cstatement_timeout=8000 -clock_timeout=4000"})
    observer = f.create_engine(args.url, poolclass=f.NullPool, connect_args={"connect_timeout": 5})
    evidence = {"samples": [], "passed": False, "warmups": 5, "measured": 50,
        "percentiles": "nearest rank; P99 with 50 samples is the maximum",
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
            "sqlalchemy": sqlalchemy.__version__, "uvicorn": f.uvicorn.__version__,
            "cpu": platform.processor(), "logical_cpus": __import__('os').cpu_count()},
        "workload": {"question": QUESTION, "document": CONTENT, "history": HISTORY,
            "answer_mode": "smart", "session_type": "questioning", "learning_mode": "direct",
            "citation_audit": False, "embedding": "fastembed", "llm_backend": "provider"}}
    evidence["source_hashes"] = {p: hashlib.sha256((f.BACKEND / p).read_bytes()).hexdigest()
        for p in ("app/api/routes/chat.py", "app/services/chat/answers.py", "app/services/knowledge/retrieval.py",
                  "app/db/session.py", "app/api/routes/knowledge.py")}
    pool = engine.pool
    evidence["pool"] = {"class": type(pool).__name__, "size": pool.size(), "max_overflow": pool._max_overflow,
        "timeout": pool.timeout(), "recycle": pool._recycle, "pre_ping": pool._pre_ping,
        "reset_on_return": str(pool._reset_on_return)}
    current = {}
    closed, ready = threading.Event(), threading.Event()
    server = thread = sock = None

    def mark(key):
        current.setdefault("timestamps", {})[key] = time.perf_counter()

    def wrap(fn, name):
        def call(*a, **kw):
            mark(name + "_start")
            result = fn(*a, **kw)
            mark(name + "_end")
            if name == "retrieval":
                current["retrieved"] = result
            if name == "routing":
                current["decision"] = result
            return result
        return call

    def mock(messages, key, url, model, **kwargs):
        mark("llm_mock_start")
        context = json.loads(messages[1]["content"])
        assert context["question"] == QUESTION and context["history"] == HISTORY
        assert len(context["evidence"]) == 1 and context["evidence"][0]["content"] == CONTENT
        current["llm_calls"] = current.get("llm_calls", 0) + 1
        current["prompt_hash"] = hashlib.sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()
        result = json.dumps({"answer": ANSWER, "citations": [1], "basis": "grounded"})
        mark("llm_mock_end")
        return result

    class ObservedSession(f.Session):
        def commit(self):
            mark("commit_start")
            super().commit()
            mark("commit_end")

    try:
        with observer.connect() as db:
            assert db.scalar(f.text("SHOW data_directory")) == args.pgdata
            assert db.scalar(f.text("SELECT current_database()")) == parsed.database
            assert db.scalar(f.text("SELECT count(*) FROM pg_tables WHERE schemaname='public'")) == 0
            evidence["environment"].update(postgres=db.scalar(f.text("SELECT version()")),
                isolation=db.scalar(f.text("SHOW transaction_isolation")))
        f.Base.metadata.create_all(engine)
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            model_cache = f.BACKEND.parent / "data/mvp-test/models/models--Qdrant--paraphrase-multilingual-MiniLM-L12-v2-onnx-Q"
            shutil.copytree(model_cache, Path(folder) / "models" / model_cache.name)
            evidence["model_hashes"] = {str(p.relative_to(model_cache)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in model_cache.rglob("*") if p.is_file() and p.suffix in (".onnx", ".json")}
            for name, value in {"LOCAL_DATA_DIR": folder, "EMBEDDING_BACKEND": "fastembed",
                "LLM_BACKEND": "provider", "RETRIEVAL_MIN_SCORE": 0.35}.items():
                stack.enter_context(patch.object(settings, name, value))
            evidence["workload"].update(embedding_model=settings.EMBEDDING_MODEL,
                retrieval_min_score=settings.RETRIEVAL_MIN_SCORE, lexical_rescue=settings.RETRIEVAL_LEXICAL_RESCUE,
                runtime_metrics=settings.RUNTIME_METRICS_ENABLED)
            path = Path(folder) / "synthetic.txt"
            path.write_text(CONTENT, encoding="utf-8")
            # Actual offline embedding subprocess; no embedding/retrieval stub.
            chunks = retrieval.run_worker({"path": str(path)}, settings.INDEX_TIMEOUT_SECONDS)["chunks"]
            assert len(chunks) == 1 and chunks[0]["content"] == CONTENT
            uid, wid, did = uuid.uuid4(), str(uuid.uuid4()), str(uuid.uuid4())
            sessions = [str(uuid.uuid4()) for _ in range(55)]
            with f.Session(engine) as db:
                db.add(f.User(id=uid, email=f"baseline-{uid.hex}@example.com", password_hash="unused-synthetic"))
                db.flush()
                db.add(f.Workspace(id=wid, user_id=uid, name="Fixed baseline"))
                db.add(UserLLMSetting(id=str(uuid.uuid4()), user_id=uid, provider="benchmark",
                    base_url="https://integrate.api.nvidia.com/v1", model_name="deterministic-mock",
                    encrypted_api_key=encrypt_text("synthetic-never-sent")))
                db.flush()
                db.add(Document(id=did, workspace_id=wid, name="transactions.txt", storage_key="synthetic.txt",
                    size_bytes=len(CONTENT), status="ready", chunk_count=1, embedding_model=retrieval.model_id()))
                db.flush()
                db.add(Chunk(document_id=did, ordinal=0, **chunks[0]))
                base = datetime(2020, 1, 1, tzinfo=timezone.utc)
                for sid in sessions:
                    db.add(ChatSession(id=sid, user_id=uid, title="Fixed history"))
                db.flush()
                for sid in sessions:
                    db.add(SessionWorkspace(session_id=sid, workspace_id=wid))
                    db.add(SessionProfile(session_id=sid, session_type="questioning"))
                    for index, message in enumerate(HISTORY):
                        db.add(Message(id=str(uuid.uuid4()), session_id=sid, created_at=base + timedelta(seconds=index), **message))
                db.commit()
            factory = f.sessionmaker(bind=engine, class_=ObservedSession, autocommit=False, autoflush=False)

            def database():
                try:
                    with factory() as db:
                        yield db
                finally:
                    closed.set()

            for module, name, stage in ((chat, "retrieve", "retrieval"), (chat, "decide", "routing"),
                (chat, "grounded_answer", "answer_pipeline"), (answers, "provider_completion", "provider_wrapper"),
                (retrieval, "run_worker", "embedding_worker")):
                stack.enter_context(patch.object(module, name, wrap(getattr(module, name), stage)))
            stack.enter_context(patch.object(completion, "generate_response", mock))
            app = f.create_app()
            app.dependency_overrides[f.get_db] = database
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

            class Server(f.uvicorn.Server):
                async def startup(self, sockets=None):
                    await super().startup(sockets=sockets)
                    ready.set()

            server = Server(f.uvicorn.Config(app, workers=1, access_log=False, log_level="error",
                limit_concurrency=16, timeout_graceful_shutdown=5))
            thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
            thread.start()
            assert ready.wait(10) and server.started
            token = f.create_access_token({"sub": str(uid)})
            with f.httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=45, trust_env=False) as client:
                for index, sid in enumerate(sessions):
                    current = {"index": index, "warmup": index < 5, "request_id": str(uuid.uuid4()), "session_id": sid}
                    evidence["samples"].append(current)
                    closed.clear()
                    mark("http_start")
                    response = client.post("/api/chat", headers={"Authorization": f"Bearer {token}"}, json={
                        "message": QUESTION, "workspace_id": wid, "session_id": sid,
                        "request_id": current["request_id"], "answer_mode": "smart", "ui_locale": "en"})
                    mark("http_end")
                    assert closed.wait(5)
                    current.update(status=response.status_code, response=response.json())
                    assert response.status_code == 200, response.text
                    body = response.json()
                    assert body["answer"] == ANSWER and body["answer_basis"] == "grounded"
                    assert len(body["sources"]) == 1 and body["sources"][0]["document_id"] == did
                    assert current["llm_calls"] == 1 and current["decision"]["action"] == "retrieve"
                    with f.Session(observer) as db:
                        turn = db.get(ChatTurn, current["request_id"])
                        msgs = db.query(Message).filter_by(session_id=sid).all()
                        assistant = next(m for m in msgs if m.content == ANSWER)
                        ev = db.get(MessageEvidence, assistant.id)
                        assert turn.response == body and len(msgs) == 4 and ev.sources == body["sources"]
                        assert "route=retrieve" in ev.ai_mode
                    current["valid"] = True
                    t = current["timestamps"]
                    spans = {"http": ("http_start", "http_end"), "routing": ("routing_start", "routing_end"),
                        "retrieval": ("retrieval_start", "retrieval_end"),
                        "embedding_worker_nested": ("embedding_worker_start", "embedding_worker_end"),
                        "prompt_preparation": ("answer_pipeline_start", "provider_wrapper_start"),
                        "provider_wrapper": ("provider_wrapper_start", "provider_wrapper_end"),
                        "llm_mock_nested": ("llm_mock_start", "llm_mock_end"),
                        "answer_validation_composition": ("provider_wrapper_end", "answer_pipeline_end"),
                        "post_generation_persistence": ("answer_pipeline_end", "commit_end"),
                        "commit_nested": ("commit_start", "commit_end")}
                    current["ms"] = {k: (t[end] - t[begin]) * 1000 for k, (begin, end) in spans.items()}
                    current["ms"]["other_http"] = current["ms"]["http"] - sum(current["ms"][k] for k in
                        ("routing", "retrieval", "prompt_preparation", "provider_wrapper", "answer_validation_composition", "post_generation_persistence"))
                    if index % 10 == 4:
                        print(f"Completed {index + 1}/55 requests", flush=True)
            measured = evidence["samples"][5:]
            evidence["summary"] = {k: distribution([s["ms"][k] for s in measured]) for k in measured[0]["ms"]}
            evidence["batch_means_ms"] = [statistics.mean(s["ms"]["http"] for s in measured[i:i+10]) for i in range(0, 50, 10)]
            assert len({s["prompt_hash"] for s in evidence["samples"]}) == 1
            evidence["success_rate"] = sum(s["valid"] for s in measured) / len(measured)
            evidence["passed"] = True
    except BaseException:
        evidence["error"] = traceback.format_exc()
        raise
    finally:
        if server:
            server.should_exit = True
        if thread:
            thread.join(10)
            evidence["api_stopped"] = not thread.is_alive()
        if sock:
            sock.close()
        engine.dispose()
        observer.dispose()
        args.output.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(json.dumps({"passed": evidence["passed"], "summary": evidence.get("summary")}))


if __name__ == "__main__":
    main()
