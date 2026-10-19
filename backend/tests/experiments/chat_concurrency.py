"""Ten-user deterministic TCP Chat admission test; isolated PG16 only.

Uses existing synthetic settings bootstrap. Only LLM generation is stubbed;
the semaphore observer delegates every acquire/release to the original object.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
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
from chat_worker_ab_block import rss
from app.services.knowledge import windows_job as native
from app.services.knowledge.query_supervisor import QueryWorkerSupervisor

f = b.f
request_key = ContextVar("experiment_request", default=None)


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
    evidence = {"passed": False, "events": [], "requests": [], "rounds": [],
                "head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "git_status": subprocess.check_output(["git", "status", "--short"], text=True),
                "source_before": hashes(), "python": platform.python_version(),
                "platform": platform.platform(), "llm_delay_seconds": 2,
                "api_workers": 1, "uvicorn_limit_concurrency": 16, "round_count": 3}
    log_lock = threading.Lock()
    rows = {}
    owner = None
    entered, release = threading.Event(), threading.Event()
    held_request = None
    original_gate = b.chat.chat_slot
    started = time.perf_counter()
    def record(event, **values):
        with log_lock:
            evidence["events"].append({"seq": len(evidence["events"]), "t": time.perf_counter()-started,
                                       "request_id": request_key.get(), "event": event, **values})
    class GateObserver:
        def acquire(self, *a, **kw):
            nonlocal owner
            acquired = original_gate.acquire(*a, **kw)
            with log_lock:
                if acquired:
                    assert owner is None
                    owner = request_key.get()
                current_owner = owner
            record("slot_acquire", acquired=acquired, owner=current_owner)
            return acquired
        def release(self):
            nonlocal owner
            # Record under observer mutex before releasing the actual semaphore.
            with log_lock:
                assert owner == request_key.get()
                owner = None
                original_gate.release()
            record("slot_release")
    def wrap(fn, name):
        def call(*a, **kw):
            record(name + "_start")
            value = fn(*a, **kw)
            extra = {}
            if name == "embedding":
                extra = {"vector_hash": hashlib.sha256(json.dumps(value).encode()).hexdigest(), "dimensions": len(value)}
            record(name + "_end", **extra)
            return value
        return call
    def mock(messages, *a, **kw):
        rid = request_key.get()
        context = json.loads(messages[1]["content"])
        assert context["question"] == b.QUESTION and context["history"] == b.HISTORY
        assert len(context["evidence"]) == 1 and context["evidence"][0]["content"] == b.CONTENT
        record("llm_start")
        deadline = time.perf_counter() + 2
        if rid == held_request:
            with log_lock:
                assert owner == rid
            entered.set()
            assert release.wait(12), "Controller did not release held request"
            record("controller_released", extra_hold_ms=max(0, time.perf_counter()-deadline)*1000)
        # Fixed simulated provider delay, never used to establish request ordering.
        threading.Event().wait(max(0, deadline-time.perf_counter()))
        record("llm_end")
        return json.dumps({"answer": b.ANSWER, "citations": [1], "basis": "grounded"})
    class ObservedSession(f.Session):
        def commit(self):
            record("commit_start")
            super().commit()
            record("commit_end")
    engine = f.create_engine(args.url, connect_args={"connect_timeout": 5,
        "options": "-cstatement_timeout=8000 -clock_timeout=4000"})
    observer = f.create_engine(args.url, poolclass=f.NullPool, connect_args={"connect_timeout": 5})
    server = thread = sock = None
    handles = []
    verified = False
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
        pool = engine.pool
        evidence["pool"] = {"size": pool.size(), "max_overflow": pool._max_overflow, "timeout": pool.timeout()}
        with tempfile.TemporaryDirectory(prefix="chat-concurrency-", dir=f.BACKEND.parent / ".local") as folder, ExitStack() as stack:
            cache = f.BACKEND.parent / "data/mvp-test/models/models--Qdrant--paraphrase-multilingual-MiniLM-L12-v2-onnx-Q"
            shutil.copytree(cache, Path(folder) / "models" / cache.name)
            for key, value in {"LOCAL_DATA_DIR": folder, "EMBEDDING_BACKEND": "fastembed", "LLM_BACKEND": "provider",
                "PERSISTENT_QUERY_WORKER_ENABLED": True, "STARTUP_RECOVERY_ENABLED": False,
                "WORKSPACE_DELETE_MAINTENANCE_MODE": False}.items():
                stack.enter_context(patch.object(b.settings, key, value))
            path = Path(folder) / "synthetic.txt"
            path.write_text(b.CONTENT, encoding="utf-8")
            chunks = b.retrieval.run_worker({"path": str(path)}, 90)["chunks"]
            assert len(chunks) == 1
            users = []
            with f.Session(engine) as db:
                for i in range(10):
                    uid, wid, did = uuid.uuid4(), str(uuid.uuid4()), str(uuid.uuid4())
                    user = {"uid": str(uid), "wid": wid, "did": did, "sessions": [],
                            "token": f.create_access_token({"sub": str(uid)})}
                    users.append(user)
                    db.add(f.User(id=uid, email=f"concurrency-{i}@example.com", password_hash="synthetic-unused"))
                    db.flush()
                    db.add(f.Workspace(id=wid, user_id=uid, name="Equivalent workspace"))
                    db.add(b.UserLLMSetting(id=str(uuid.uuid4()), user_id=uid, provider="benchmark",
                        base_url="https://integrate.api.nvidia.com/v1", model_name="mock",
                        encrypted_api_key=b.encrypt_text("synthetic-never-sent")))
                    db.flush()
                    db.add(b.Document(id=did, workspace_id=wid, name="transactions.txt", storage_key="synthetic.txt",
                        size_bytes=len(b.CONTENT), status="ready", chunk_count=1, embedding_model=b.retrieval.model_id()))
                    db.flush()
                    db.add(b.Chunk(document_id=did, ordinal=0, **chunks[0]))
                    for _ in range(4):
                        sid = str(uuid.uuid4())
                        user["sessions"].append(sid)
                        db.add(b.ChatSession(id=sid, user_id=uid, title="Fixed history"))
                        db.flush()
                        db.add(b.SessionWorkspace(session_id=sid, workspace_id=wid))
                        db.add(b.SessionProfile(session_id=sid, session_type="questioning"))
                        for j, message in enumerate(b.HISTORY):
                            db.add(b.Message(id=str(uuid.uuid4()), session_id=sid,
                                created_at=datetime(2020, 1, 1, tzinfo=timezone.utc)+timedelta(seconds=j), **message))
                db.commit()
            factory = f.sessionmaker(bind=engine, class_=ObservedSession, autoflush=False)
            def database():
                rid = request_key.get()
                record("session_open")
                try:
                    with factory() as db:
                        yield db
                finally:
                    record("session_closed")
                    if rid in rows:
                        rows[rid]["closed"].set()
            stack.enter_context(patch.object(b.chat, "chat_slot", GateObserver()))
            stack.enter_context(patch.object(b.chat, "retrieve", wrap(b.chat.retrieve, "retrieval")))
            stack.enter_context(patch.object(QueryWorkerSupervisor, "embed", wrap(QueryWorkerSupervisor.embed, "embedding")))
            stack.enter_context(patch.object(b.completion, "generate_response", mock))
            app = f.create_app()
            app.dependency_overrides[f.get_db] = database
            @app.middleware("http")
            async def tracing(request, call_next):
                token = request_key.set(request.headers.get("X-Experiment-ID"))
                try:
                    record("http_entry")
                    return await call_next(request)
                finally:
                    request_key.reset(token)
            ready = threading.Event()
            class Server(f.uvicorn.Server):
                async def startup(self, sockets=None):
                    await super().startup(sockets=sockets)
                    ready.set()
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            server = Server(f.uvicorn.Config(app, workers=1, limit_concurrency=16,
                log_level="error", access_log=False, timeout_graceful_shutdown=5))
            thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
            def stop():
                release.set()
                server.should_exit = True
                thread.join(45)
                assert not thread.is_alive(), "API did not stop"
            stack.callback(stop)
            thread.start()
            assert ready.wait(45) and server.started
            worker = app.state.query_worker.worker
            handles = [native.process_handle(pid) for pid in worker.job.pids()]
            evidence["model_pid"] = worker.pid
            evidence["worker_rss"] = rss(worker.pid)
            clients = [stack.enter_context(f.httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=20, trust_env=False)) for _ in users]
            for client in clients:
                assert client.get("/api/ready").status_code == 200
            def prepare(i, round_index):
                row = {"request_id": str(uuid.uuid4()), "user_index": i, "user_id": users[i]["uid"],
                       "session_id": users[i]["sessions"][round_index], "round": round_index}
                rows[row["request_id"]] = {"closed": threading.Event()}
                evidence["requests"].append(row)
                return row
            def send(row, barrier=None):
                if barrier:
                    barrier.wait(5)
                i, rid = row["user_index"], row["request_id"]
                row["start"] = time.perf_counter()-started
                response = clients[i].post("/api/chat", headers={"Authorization": "Bearer " + users[i]["token"],
                    "X-Experiment-ID": rid}, json={"message": b.QUESTION, "workspace_id": users[i]["wid"],
                    "session_id": row["session_id"], "request_id": rid, "answer_mode": "smart", "ui_locale": "en"})
                row.update(end=time.perf_counter()-started, status=response.status_code, body=response.json())
                row["latency_ms"] = (row["end"]-row["start"])*1000
                assert rows[rid]["closed"].wait(5)
                return row
            def verify(row):
                i = row["user_index"]
                with f.Session(observer) as db:
                    msgs = db.query(b.Message).filter_by(session_id=row["session_id"]).order_by(b.Message.created_at).all()
                    turn = db.get(b.ChatTurn, row["request_id"])
                    evs = db.query(b.MessageEvidence).filter(b.MessageEvidence.message_id.in_([m.id for m in msgs])).all()
                    if row["status"] == 200:
                        assert row["body"]["answer"] == b.ANSWER and row["body"]["answer_basis"] == "grounded"
                        assert len(msgs) == 4 and turn.response == row["body"] and str(turn.user_id) == users[i]["uid"]
                        assert len(evs) == 1 and evs[0].sources == row["body"]["sources"]
                        assert evs[0].sources[0]["document_id"] == users[i]["did"] and "route=retrieve" in evs[0].ai_mode
                    else:
                        assert row["status"] == 429 and row["body"]["detail"] == "A chat is already running locally. Please try again shortly."
                        assert turn is None and not evs and len(msgs) == 2
                    assert [{"role": m.role, "content": m.content} for m in msgs[:2]] == b.HISTORY
                    assert db.get(b.SessionWorkspace, row["session_id"]).workspace_id == users[i]["wid"]
                    assert db.get(b.SessionProfile, row["session_id"]).session_type == "questioning"
                row["integrity"] = True
            sequential = [prepare(i, 0) for i in range(10)]
            for row in sequential:
                send(row)
                assert row["status"] == 200
                verify(row)
            print("Sequential 10/10 verified", flush=True)
            for round_index in range(1, 4):
                entered.clear()
                release.clear()
                batch = [prepare(i, round_index) for i in range(10)]
                # Rotate the successful user across rounds; no request retries.
                a = batch[round_index-1]
                held_request = a["request_id"]
                with ThreadPoolExecutor(max_workers=10) as executor:
                    future_a = executor.submit(send, a)
                    try:
                        assert entered.wait(10), "A did not enter Mock LLM"
                        with log_lock:
                            assert owner == held_request
                        record("controller_confirmed_lock", held_request=held_request)
                        barrier = threading.Barrier(10)
                        others = [executor.submit(send, row, barrier) for row in batch if row is not a]
                        barrier.wait(5)
                        for future in others:
                            assert future.result(10)["status"] == 429
                        assert not future_a.done()
                    finally:
                        release.set()
                    assert future_a.result(15)["status"] == 200
                for row in batch:
                    verify(row)
                window = max(r["end"] for r in batch)-min(r["start"] for r in batch)
                evidence["rounds"].append({"round": round_index, "window_seconds": window,
                    "http_rps": 10/window, "successful_throughput": 1/window,
                    "success_rate": .1, "rate_429": .9})
                print(f"Round {round_index}: 1 success + 9 rejected, integrity verified", flush=True)
            held_request = None
            # Assert actual execution paths and lock/commit ordering, not just status.
            for row in evidence["requests"]:
                events = [e for e in evidence["events"] if e["request_id"] == row["request_id"]]
                row["path"] = [e["event"] for e in events]
                acquisitions = [e for e in events if e["event"] == "slot_acquire"]
                assert len(acquisitions) == 1 and acquisitions[0]["acquired"] == (row["status"] == 200)
                if row["status"] == 429:
                    assert not set(row["path"]) & {"retrieval_start", "embedding_start", "llm_start", "commit_start", "slot_release"}
                else:
                    assert row["path"].count("llm_start") == 1
                    assert row["path"].index("commit_end") < row["path"].index("slot_release")
            with f.Session(observer) as db:
                evidence["final_counts"] = {"users": db.query(f.User).count(), "workspaces": db.query(f.Workspace).count(),
                    "sessions": db.query(b.ChatSession).count(), "messages": db.query(b.Message).count(),
                    "turns": db.query(b.ChatTurn).count(), "evidence": db.query(b.MessageEvidence).count(),
                    "ready_documents": db.query(b.Document).filter_by(status="ready").count(), "chunks": db.query(b.Chunk).count()}
            assert evidence["final_counts"] == dict(users=10, workspaces=10, sessions=40, messages=106,
                turns=13, evidence=13, ready_documents=10, chunks=10)
            assert len({e["vector_hash"] for e in evidence["events"] if e["event"] == "embedding_end"}) == 1
            evidence["supervisor"] = app.state.query_worker.snapshot()
            assert evidence["supervisor"]["pid"] == evidence["model_pid"] and evidence["supervisor"]["restarts"] == 0
            assert clients[0].get("/api/ready").status_code == 200
            for label, selected in {"sequential": sequential, "concurrent": evidence["requests"][10:]}.items():
                windows = (sum(r["window_seconds"] for r in evidence["rounds"]) if label == "concurrent"
                           else max(r["end"] for r in selected)-min(r["start"] for r in selected))
                successes = [r for r in selected if r["status"] == 200]
                rejected = [r for r in selected if r["status"] == 429]
                evidence[label] = {"n": len(selected), "window_seconds": windows, "http_rps": len(selected)/windows,
                    "successful_throughput": len(successes)/windows, "success_rate": len(successes)/len(selected),
                    "rate_429": len(rejected)/len(selected), "success_latency": b.distribution([r["latency_ms"] for r in successes]),
                    "rejected_latency": b.distribution([r["latency_ms"] for r in rejected]) if rejected else None}
            stop()
            evidence["managed_exit_checks"] = [native.exit_status(h, 5000) for h in handles]
            assert all(e["exited"] for e in evidence["managed_exit_checks"])
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
            thread.join(45)
            evidence["api_stopped"] = not thread.is_alive()
        if sock:
            sock.close()
        for handle in handles:
            native.close(handle)
        if verified and (thread is None or not thread.is_alive()):
            f.Base.metadata.drop_all(engine)
            evidence["schema_removed"] = True
        engine.dispose()
        observer.dispose()
        (args.output / "result.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
