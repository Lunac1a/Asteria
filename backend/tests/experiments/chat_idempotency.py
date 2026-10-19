"""Real TCP/PG Chat idempotency baseline, with event-controlled external LLM.

Create a fresh dedicated cluster using workspace_concurrency_pg.sh; pass exact
PGDATA and clean it with workspace_cross_cleanup.sh even if assertions fail.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import traceback
from unittest.mock import patch
import uuid

import chat_baseline as b

f = b.f
label = ContextVar("idempotency_experiment_label", default="setup")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--pgdata", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    url = f.make_url(args.url)
    assert url.drivername == "postgresql+psycopg" and url.host == "127.0.0.1"
    assert url.port == 55489 and url.database == "workspace_concurrency_test"
    assert args.pgdata.startswith("/tmp/asteria-concurrency-cross-")
    def hashes():
        return {str(p.relative_to(f.BACKEND)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (f.BACKEND / "app").rglob("*.py")}
    evidence = {"passed": False, "events": [], "responses": {}, "snapshots": {},
        "source_before": hashes(), "python": platform.python_version(), "platform": platform.platform(),
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "git_status": subprocess.check_output(["git", "status", "--short"], text=True),
        "http": "real loopback TCP, one Uvicorn worker", "embedding": "real offline FastEmbed disposable",
        "llm_calls": 0}
    entered, release, ready = threading.Event(), threading.Event(), threading.Event()
    closed = {name: threading.Event() for name in ("A", "B_concurrent", "B_replay", "B_conflict")}
    log_lock = threading.Lock()
    origin = time.perf_counter()
    def record(event):
        with log_lock:
            evidence["events"].append({"seq": len(evidence["events"]), "t": time.perf_counter()-origin,
                                        "request": label.get(), "event": event})
    def mock(messages, *a, **kw):
        context = json.loads(messages[1]["content"])
        assert label.get() == "A" and context["question"] == b.QUESTION and context["history"] == []
        assert len(context["evidence"]) == 1 and context["evidence"][0]["content"] == b.CONTENT
        with log_lock:
            evidence["llm_calls"] += 1
        record("llm_enter")
        entered.set()
        assert release.wait(15), "Controller release deadline exceeded"
        record("llm_exit")
        return json.dumps({"answer": b.ANSWER, "citations": [1], "basis": "grounded"})
    class ObservedSession(f.Session):
        def commit(self):
            record("commit_start")
            super().commit()
            record("commit_returned")
    engine = f.create_engine(args.url, connect_args={"connect_timeout": 5,
        "options": "-cstatement_timeout=8000 -clock_timeout=4000"})
    observer = f.create_engine(args.url, poolclass=f.NullPool, connect_args={"connect_timeout": 5})
    server = thread = sock = None
    verified = False
    models = (b.ChatSession, b.SessionWorkspace, b.SessionProfile, b.Message, b.ChatTurn, b.MessageEvidence)
    def snapshot(name):
        with observer.connect() as db:
            data = {}
            for model in models:
                table = model.__table__
                rows = [dict(row) for row in db.execute(table.select()).mappings()]
                data[table.name] = sorted(rows, key=lambda row: str(next(iter(row.values()))))
        data = json.loads(json.dumps(data, default=str))
        evidence["snapshots"][name] = data
        return data
    try:
        with observer.connect() as db:
            assert db.scalar(f.text("SHOW data_directory")) == args.pgdata
            assert db.scalar(f.text("SELECT current_database()")) == url.database
            assert db.scalar(f.text("SELECT count(*) FROM pg_tables WHERE schemaname='public'")) == 0
            version = db.scalar(f.text("SHOW server_version"))
            assert version.startswith("16.")
            evidence["postgres"] = {"version": version, "pgdata": args.pgdata,
                                    "isolation": db.scalar(f.text("SHOW transaction_isolation"))}
        verified = True
        f.Base.metadata.create_all(engine)
        with tempfile.TemporaryDirectory(prefix="chat-idempotency-", dir=f.BACKEND.parent / ".local") as folder, ExitStack() as stack:
            cache = f.BACKEND.parent / "data/mvp-test/models/models--Qdrant--paraphrase-multilingual-MiniLM-L12-v2-onnx-Q"
            shutil.copytree(cache, Path(folder) / "models" / cache.name)
            for key, value in {"LOCAL_DATA_DIR": folder, "EMBEDDING_BACKEND": "fastembed", "LLM_BACKEND": "provider",
                "PERSISTENT_QUERY_WORKER_ENABLED": False, "STARTUP_RECOVERY_ENABLED": False,
                "WORKSPACE_DELETE_MAINTENANCE_MODE": False}.items():
                stack.enter_context(patch.object(b.settings, key, value))
            path = Path(folder) / "synthetic.txt"
            path.write_text(b.CONTENT, encoding="utf-8")
            chunks = b.retrieval.run_worker({"path": str(path)}, 90)["chunks"]
            assert len(chunks) == 1
            uid, wid, did = uuid.uuid4(), str(uuid.uuid4()), str(uuid.uuid4())
            rid = str(uuid.uuid4())  # Fixed for all four requests in this run.
            payload = {"message": b.QUESTION, "workspace_id": wid, "request_id": rid,
                       "session_type": "questioning", "answer_mode": "smart", "learning_mode": "direct", "ui_locale": "en"}
            assert "session_id" not in payload
            evidence["payload"] = payload
            with f.Session(engine) as db:
                db.add(f.User(id=uid, email="idempotency@example.com", password_hash="synthetic-unused"))
                db.flush()
                db.add(f.Workspace(id=wid, user_id=uid, name="Isolated idempotency test"))
                db.add(b.UserLLMSetting(id=str(uuid.uuid4()), user_id=uid, provider="benchmark",
                    base_url="https://integrate.api.nvidia.com/v1", model_name="mock",
                    encrypted_api_key=b.encrypt_text("synthetic-never-sent")))
                db.flush()
                db.add(b.Document(id=did, workspace_id=wid, name="transactions.txt", storage_key="synthetic.txt",
                    size_bytes=len(b.CONTENT), status="ready", chunk_count=1, embedding_model=b.retrieval.model_id()))
                db.flush()
                db.add(b.Chunk(document_id=did, ordinal=0, **chunks[0]))
                db.commit()
            factory = f.sessionmaker(bind=engine, class_=ObservedSession, autoflush=False)
            def database():
                name = label.get()
                record("session_open")
                try:
                    with factory() as db:
                        yield db
                finally:
                    record("session_closed")
                    if name in closed:
                        closed[name].set()
            stack.enter_context(patch.object(b.completion, "generate_response", mock))
            original_retrieve = b.chat.retrieve
            def retrieve(*a, **kw):
                record("retrieval")
                return original_retrieve(*a, **kw)
            stack.enter_context(patch.object(b.chat, "retrieve", retrieve))
            app = f.create_app()
            app.dependency_overrides[f.get_db] = database
            @app.middleware("http")
            async def tracing(request, call_next):
                token = label.set(request.headers.get("X-Experiment-Label", "setup"))
                try:
                    return await call_next(request)
                finally:
                    label.reset(token)
            class Server(f.uvicorn.Server):
                async def startup(self, sockets=None):
                    await super().startup(sockets=sockets)
                    ready.set()
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            server = Server(f.uvicorn.Config(app, workers=1, limit_concurrency=16, access_log=False,
                log_level="error", timeout_graceful_shutdown=5))
            thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
            def stop():
                release.set()
                server.should_exit = True
                thread.join(30)
                assert not thread.is_alive(), "API shutdown unconfirmed"
            stack.callback(stop)
            thread.start()
            assert ready.wait(15) and server.started
            token = f.create_access_token({"sub": str(uid)})
            clients = [stack.enter_context(f.httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=25, trust_env=False)) for _ in range(2)]
            assert clients[0].get("/api/ready").status_code == 200
            def send(name, client, body):
                start = time.perf_counter()
                response = client.post("/api/chat", headers={"Authorization": "Bearer " + token,
                    "X-Experiment-Label": name}, json=body)
                evidence["responses"][name] = {"status": response.status_code, "body": response.json(),
                                               "latency_ms": (time.perf_counter()-start)*1000}
                assert closed[name].wait(5)
                return response
            initial = snapshot("initial")
            assert all(not values for values in initial.values())
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(send, "A", clients[0], payload)
                try:
                    assert entered.wait(12), "A did not reach Mock LLM"
                    assert not future.done()
                    concurrent = send("B_concurrent", clients[1], payload)
                    assert concurrent.status_code == 429
                    assert concurrent.json()["detail"] == "A chat is already running locally. Please try again shortly."
                    assert snapshot("while_A_paused_after_B") == initial
                    assert evidence["llm_calls"] == 1 and not future.done()
                finally:
                    release.set()
                a = future.result(20)
            assert a.status_code == 200 and a.json()["answer"] == b.ANSWER and a.json()["answer_basis"] == "grounded"
            committed = snapshot("after_A_commit")
            expected = {model.__tablename__: count for model, count in zip(models, (1, 1, 1, 2, 1, 1), strict=True)}
            assert {name: len(values) for name, values in committed.items()} == expected
            sid = a.json()["session_id"]
            assert committed[b.ChatSession.__tablename__][0]["id"] == sid
            assert committed[b.ChatSession.__tablename__][0]["user_id"] == str(uid)
            assert committed[b.SessionWorkspace.__tablename__][0]["workspace_id"] == wid
            turn = committed[b.ChatTurn.__tablename__][0]
            assert turn["request_id"] == rid and turn["session_id"] == sid and turn["response"] == a.json()
            messages = committed[b.Message.__tablename__]
            assert {(m["role"], m["content"]) for m in messages} == {("user", b.QUESTION), ("assistant", b.ANSWER)}
            ev = committed[b.MessageEvidence.__tablename__][0]
            assert ev["message_id"] == next(m["id"] for m in messages if m["role"] == "assistant")
            assert ev["sources"] == a.json()["sources"] and ev["sources"][0]["document_id"] == did
            replay = send("B_replay", clients[1], payload)
            assert replay.status_code == 200 and replay.json() == a.json()
            assert snapshot("after_replay") == committed and evidence["llm_calls"] == 1
            conflict = send("B_conflict", clients[1], {**payload, "message": "What does transaction isolation mean?"})
            assert conflict.status_code == 409 and conflict.json()["detail"] == "Request ID already used for a different turn"
            assert snapshot("after_conflict") == committed and evidence["llm_calls"] == 1
            for name in closed:
                events = [e["event"] for e in evidence["events"] if e["request"] == name]
                evidence["responses"][name]["path"] = events
                if name == "A":
                    assert events.count("commit_returned") == events.count("llm_enter") == 1
                else:
                    assert not set(events) & {"retrieval", "llm_enter", "commit_start"}
            evidence["final_counts"] = expected
            assert clients[0].get("/api/ready").status_code == 200
            stop()
        evidence["temporary_directory_removed"] = not Path(folder).exists()
        evidence["source_after"] = hashes()
        assert evidence["source_before"] == evidence["source_after"]
        evidence["passed"] = True
    except BaseException:
        evidence["error"] = traceback.format_exc()
        raise
    finally:
        release.set()
        if server:
            server.should_exit = True
        if thread:
            thread.join(30)
            evidence["api_stopped"] = not thread.is_alive()
        if sock:
            sock.close()
        if verified and (thread is None or not thread.is_alive()):
            f.Base.metadata.drop_all(engine)
            evidence["schema_removed"] = True
        engine.dispose()
        observer.dispose()
        (args.output / "result.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
