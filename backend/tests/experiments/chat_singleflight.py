"""Real TCP/PG Single-flight scenarios with deterministic failures and synchronization.

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
from fastapi import HTTPException

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
    ready = threading.Event()
    closed, entered, releases, joined = {}, {}, {}, {}
    before_slot, try_slot = threading.Event(), threading.Event()
    fault = None
    evidence["cases"] = []
    log_lock = threading.Lock()
    origin = time.perf_counter()
    def record(event):
        with log_lock:
            evidence["events"].append({"seq": len(evidence["events"]), "t": time.perf_counter()-origin,
                                        "request": label.get(), "event": event})
    def mock(messages, *a, **kw):
        context = json.loads(messages[1]["content"])
        assert context["question"] == b.QUESTION and context["history"] == []
        assert len(context["evidence"]) == 1 and context["evidence"][0]["content"] == b.CONTENT
        with log_lock:
            evidence["llm_calls"] += 1
        record("llm_enter")
        entered[label.get()].set()
        assert releases[label.get()].wait(15), "Controller release deadline exceeded"
        if fault == "generation_failure" and label.get() == "generation_failure_A":
            raise HTTPException(502, "Injected generation failure")
        record("llm_exit")
        return json.dumps({"answer": b.ANSWER, "citations": [1], "basis": "grounded"})
    class ObservedSession(f.Session):
        def commit(self):
            record("commit_start")
            if fault == "commit_failure" and label.get() == "commit_failure_A":
                self.execute(f.text("SET LOCAL asteria.fail_commit = 'on'"))
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
        with engine.begin() as db:
            db.execute(f.text("""CREATE OR REPLACE FUNCTION experiment_fail_commit() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF current_setting('asteria.fail_commit', true) = 'on' THEN
                        RAISE EXCEPTION 'Injected deferred COMMIT failure';
                    END IF;
                    RETURN NEW;
                END $$"""))
            db.execute(f.text("""CREATE CONSTRAINT TRIGGER experiment_commit_guard
                AFTER INSERT ON chat_turns DEFERRABLE INITIALLY DEFERRED
                FOR EACH ROW EXECUTE FUNCTION experiment_fail_commit()"""))
        with tempfile.TemporaryDirectory(prefix="chat-singleflight-", dir=f.BACKEND.parent / ".local") as folder, ExitStack() as stack:
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
            original_register = b.chat.registry.register
            def register(*a, **kw):
                flight, leader = original_register(*a, **kw)
                record("registered_leader" if leader else "joined_follower")
                if not leader:
                    joined[label.get()].set()
                return flight, leader
            stack.enter_context(patch.object(b.chat.registry, "register", register))
            original_execute = b.chat.execute_chat
            def execute(*a, **kw):
                if label.get() == "busy_A":
                    before_slot.set()
                    assert try_slot.wait(10)
                return original_execute(*a, **kw)
            stack.enter_context(patch.object(b.chat, "execute_chat", execute))
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
                [event.set() for event in releases.values()]
                try_slot.set()
                server.should_exit = True
                thread.join(30)
                assert not thread.is_alive(), "API shutdown unconfirmed"
            stack.callback(stop)
            thread.start()
            assert ready.wait(15) and server.started
            token = f.create_access_token({"sub": str(uid)})
            clients = [stack.enter_context(f.httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=25, trust_env=False)) for _ in range(6)]
            assert clients[0].get("/api/ready").status_code == 200
            def send(name, client, body):
                start = time.perf_counter()
                response = client.post("/api/chat", headers={"Authorization": "Bearer " + token,
                    "X-Experiment-Label": name}, json=body)
                try:
                    body = response.json()
                except ValueError:
                    body = {"text": response.text}
                evidence["responses"][name] = {"status": response.status_code, "body": body,
                                               "latency_ms": (time.perf_counter()-start)*1000}
                assert closed[name].wait(5)
                return response
            def prepare(name):
                closed[name] = threading.Event()
                entered[name] = threading.Event()
                releases[name] = threading.Event()
                joined[name] = threading.Event()
            def counts(state):
                return {key: len(value) for key, value in state.items()}
            def assert_delta(before, after, n):
                expected = (n, n, n, 2*n, n, n)
                for model, delta in zip(models, expected, strict=True):
                    assert len(after[model.__tablename__])-len(before[model.__tablename__]) == delta
            with ThreadPoolExecutor(max_workers=6) as executor:
                for case in ("success", "conflict", "timeout", "busy", "generation_failure", "commit_failure"):
                    fault = case
                    before_slot.clear()
                    try_slot.clear()
                    names = {suffix: case + "_" + suffix for suffix in ("A", "B", "C", "D", "E", "overflow", "conflict", "replay", "retry_A")}
                    for name in names.values():
                        prepare(name)
                    body = {**payload, "request_id": str(uuid.uuid4())}
                    before = snapshot(case + "_before")
                    calls_before = evidence["llm_calls"]
                    blocker = None
                    if case == "busy":
                        prepare("busy_blocker")
                        blocker = executor.submit(send, "busy_blocker", clients[3], {**payload, "request_id": str(uuid.uuid4())})
                        assert entered["busy_blocker"].wait(10)
                    a = executor.submit(send, names["A"], clients[0], body)
                    try:
                        assert (before_slot if case == "busy" else entered[names["A"]]).wait(10)
                        if case == "conflict":
                            response = send(names["conflict"], clients[3], {**body, "message": "Different question"})
                            assert response.status_code == 409
                            assert not a.done()
                        with patch.object(b.settings, "CHAT_SINGLEFLIGHT_WAIT_SECONDS", .2 if case == "timeout" else 10):
                            follower = executor.submit(send, names["B"], clients[1], body)
                            assert joined[names["B"]].wait(5)
                            if case == "timeout":
                                assert follower.result(5).status_code == 504
                                assert not a.done() and b.chat.registry.running_count() == 1
                                assert snapshot(case + "_after_timeout") == before
                                b.settings.CHAT_SINGLEFLIGHT_WAIT_SECONDS = 10
                            else:
                                assert not follower.done()
                            second = executor.submit(send, names["C"], clients[2], body)
                            assert joined[names["C"]].wait(5)
                            extras = []
                            if case == "success":
                                for suffix, client in zip(("D", "E"), clients[4:6], strict=True):
                                    extras.append(executor.submit(send, names[suffix], client, body))
                                    assert joined[names[suffix]].wait(5)
                                assert b.chat.registry.follower_count() == 4
                                overflow = send(names["overflow"], clients[3], body)
                                assert overflow.status_code == 429
                                assert b.chat.registry.follower_count() == 4 and not a.done()
                                evidence["capacity"] = {"admitted": 4, "overflow_status": 429}
                            # Both followers have registered before Leader may finish.
                            if case == "busy":
                                try_slot.set()
                            else:
                                releases[names["A"]].set()
                            response_a = a.result(20)
                            response_b = follower.result(20)
                            response_c = second.result(20)
                            for extra in extras:
                                result = extra.result(20)
                                assert result.status_code == 200 and result.json() == response_a.json()
                        expected = {"busy": (429,429,429), "generation_failure": (502,502,502),
                                    "commit_failure": (500,503,503), "timeout": (200,504,200)}.get(case, (200,200,200))
                        actual = (response_a.status_code, response_b.status_code, response_c.status_code)
                        assert actual == expected, (case, actual)
                        if case == "busy":
                            assert b.chat.registry.running_count() == 1  # only unrelated blocker
                            assert snapshot(case + "_rejected") == before
                            releases["busy_blocker"].set()
                            assert blocker.result(20).status_code == 200
                        assert b.chat.registry.running_count() == 0
                        assert b.chat.registry.follower_count() == 0
                        after = snapshot(case + "_after")
                        successful = case not in {"generation_failure", "commit_failure"}
                        assert_delta(before, after, int(successful))
                        if not successful:
                            assert before == after
                        if case not in {"busy", "generation_failure", "commit_failure"}:
                            assert response_a.json() == response_c.json()
                            if case != "timeout":
                                assert response_a.json() == response_b.json()
                            replay = send(names["replay"], clients[1], body)
                            assert replay.status_code == 200 and replay.json() == response_a.json()
                            assert snapshot(case + "_after_replay") == after
                        assert evidence["llm_calls"]-calls_before == 1
                        entry = {"case": case, "leader_follower_statuses": actual, "llm_calls_before_retry": 1,
                                 "counts_before": counts(before), "counts_after": counts(after),
                                 "registry_remaining": b.chat.registry.running_count()}
                        entry["followers_remaining"] = b.chat.registry.follower_count()
                        if case in {"busy", "generation_failure", "commit_failure"}:
                            fault = None
                            releases[names["retry_A"]].set()
                            retry = send(names["retry_A"], clients[0], body)
                            assert retry.status_code == 200
                            assert_delta(after, snapshot(case + "_after_retry"), 1)
                            assert evidence["llm_calls"]-calls_before == 2
                            assert b.chat.registry.running_count() == 0
                            entry["fresh_retry_status"] = 200
                        evidence["cases"].append(entry)
                        print(case, actual, "registry empty", flush=True)
                    finally:
                        [event.set() for event in releases.values()]
                        try_slot.set()
            final = snapshot("final")
            assert counts(final) == {model.__tablename__: count for model, count in zip(models, (7,7,7,14,7,7), strict=True)}
            # Every successful turn is owned and exactly matches its durable response.
            for turn in final[b.ChatTurn.__tablename__]:
                assert turn["user_id"] == str(uid) and turn["response"]["answer"] == b.ANSWER
                sid = turn["session_id"]
                assert turn["response"]["session_id"] == sid
                msgs = [m for m in final[b.Message.__tablename__] if m["session_id"] == sid]
                assert {(m["role"],m["content"]) for m in msgs} == {("user",b.QUESTION),("assistant",b.ANSWER)}
                assistant = next(m for m in msgs if m["role"] == "assistant")
                ev = next(e for e in final[b.MessageEvidence.__tablename__] if e["message_id"] == assistant["id"])
                assert ev["sources"] == turn["response"]["sources"] and ev["sources"][0]["document_id"] == did
            for name, response in evidence["responses"].items():
                events = [e["event"] for e in evidence["events"] if e["request"] == name]
                response["path"] = events
                if name.endswith(("_B", "_C", "_D", "_E", "_overflow", "_conflict", "_replay")):
                    assert not set(events) & {"retrieval", "llm_enter", "commit_start"}
            assert b.chat.registry.running_count() == 0
            evidence["final_counts"] = counts(final)
            evidence["registry_remaining"] = 0
            evidence["followers_remaining"] = b.chat.registry.follower_count()
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
        [event.set() for event in releases.values()]
        try_slot.set()
        if server:
            server.should_exit = True
        if thread:
            thread.join(30)
            evidence["api_stopped"] = not thread.is_alive()
        if sock:
            sock.close()
        if verified and (thread is None or not thread.is_alive()):
            f.Base.metadata.drop_all(engine)
            with engine.begin() as db:
                db.execute(f.text("DROP FUNCTION IF EXISTS experiment_fail_commit()"))
            evidence["schema_removed"] = True
        engine.dispose()
        observer.dispose()
        (args.output / "result.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
