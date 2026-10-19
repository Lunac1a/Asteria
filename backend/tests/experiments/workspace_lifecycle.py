"""Isolated PostgreSQL + TCP HTTP lifecycle ordering, only LLM mocked."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
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
from app.services import workspace_lifecycle as life
from app.services import workspace_delete_recovery as recovery
from app.models.workspace_delete_operation import WorkspaceDeleteOperation as Operation

f = b.f
label = ContextVar("lifecycle_label", default="setup")
QUESTION = "Give a general explanation of database atomicity without using documents."
ANSWER = "Atomicity means that all changes in a transaction succeed together or none take effect."


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--pgdata", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    url = f.make_url(args.url)
    assert url.drivername == "postgresql+psycopg" and url.host == "127.0.0.1"
    assert url.port == 55489 and url.database == "workspace_concurrency_test"
    assert args.pgdata.startswith("/tmp/asteria-concurrency-cross-")
    args.output.mkdir(parents=True, exist_ok=False)
    engine = f.create_engine(args.url, connect_args={"connect_timeout": 5,
        "options": "-cstatement_timeout=8000 -clock_timeout=1500"})
    observer = f.create_engine(args.url, poolclass=f.NullPool, connect_args={"connect_timeout": 5})
    evidence = {"passed": False, "events": [], "cases": [], "responses": {}, "llm_calls": 0,
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "git_status": subprocess.check_output(["git", "status", "--short"], text=True),
        "environment": "One Uvicorn worker, real TCP, real auth, explicit general route, mock external LLM"}
    def hashes():
        return {str(p.relative_to(f.BACKEND)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (f.BACKEND / "app").rglob("*.py")}
    evidence["source_before"] = hashes()
    origin, lock = time.monotonic(), threading.Lock()
    def record(event):
        with lock:
            evidence["events"].append({"t": time.monotonic()-origin, "request": label.get(),
                                       "event": event, "lifecycle": life.coordinator.snapshot()})
    llm_enter, llm_release = threading.Event(), threading.Event()
    rollback_enter, rollback_release = threading.Event(), threading.Event()
    admission_enter, admission_release = threading.Event(), threading.Event()
    delete_enter, delete_release = threading.Event(), threading.Event()
    releases = (llm_release, rollback_release, admission_release, delete_release)
    case = None
    failed_commit = set()
    def mock(messages, *a, **kw):
        context = json.loads(messages[1]["content"])
        assert context["question"] == QUESTION and context["history"] == [] and context["evidence"] == []
        evidence["llm_calls"] += 1
        record("llm_enter")
        llm_enter.set()
        assert llm_release.wait(15), "LLM release timeout"
        if case == "generation_failure":
            raise HTTPException(502, "Injected generation failure")
        return json.dumps({"answer": ANSWER, "basis": "general", "citations": []})
    class ObservedSession(f.Session):
        def commit(self):
            if case == "commit_failure" and label.get().endswith("_chat"):
                self.execute(f.text("SET LOCAL asteria.fail_commit = 'on'"))
                try:
                    super().commit()
                except Exception:
                    failed_commit.add(id(self))
                    record("commit_failed")
                    raise
            else:
                super().commit()
                record("commit_returned")

        def rollback(self):
            if id(self) in failed_commit:
                record("before_rollback")
                rollback_enter.set()
                assert rollback_release.wait(15), "Rollback release timeout"
                failed_commit.remove(id(self))
            super().rollback()
            record("rollback_returned")
    original_permit = b.chat.chat_permit
    @contextmanager
    def permit(*a, **kw):
        if case == "delete_first":
            record("before_admission")
            admission_enter.set()
            assert admission_release.wait(15)
        with original_permit(*a, **kw):
            yield
    original_prepare = recovery.prepare
    def prepare(*a, **kw):
        if case == "delete_first":
            record("delete_exclusive_before_prepare")
            delete_enter.set()
            assert delete_release.wait(15)
        return original_prepare(*a, **kw)
    models = (b.ChatSession, b.SessionWorkspace, b.SessionProfile, b.Message, b.ChatTurn, b.MessageEvidence)
    def snapshot():
        with observer.connect() as db:
            counts = {m.__tablename__: db.scalar(f.text(f'SELECT count(*) FROM "{m.__tablename__}"')) for m in models}
            operations = [dict(r) for r in db.execute(Operation.__table__.select()).mappings()]
            return {"counts": counts, "operations": json.loads(json.dumps(operations, default=str))}
    verified = False
    server = thread = sock = None
    try:
        with observer.connect() as db:
            assert db.scalar(f.text("SHOW data_directory")) == args.pgdata
            assert db.scalar(f.text("SELECT current_database()")) == url.database
            assert db.scalar(f.text("SELECT count(*) FROM pg_tables WHERE schemaname='public'")) == 0
            version = db.scalar(f.text("SHOW server_version"))
            assert version.startswith("16.")
            evidence["postgres"] = {"version": version, "isolation": db.scalar(f.text("SHOW transaction_isolation")), "pgdata": args.pgdata}
        verified = True
        f.Base.metadata.create_all(engine)
        with engine.begin() as db:
            db.execute(f.text("""CREATE FUNCTION experiment_fail_commit() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  IF current_setting('asteria.fail_commit', true) = 'on' THEN
                    RAISE EXCEPTION 'Injected deferred COMMIT failure';
                  END IF;
                  RETURN NEW;
                END $$"""))
            db.execute(f.text("""CREATE CONSTRAINT TRIGGER experiment_commit_guard AFTER INSERT ON chat_turns
                DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION experiment_fail_commit()"""))
        with tempfile.TemporaryDirectory(prefix="lifecycle-", dir=f.BACKEND.parent / ".local") as folder, ExitStack() as stack:
            root = Path(folder)
            (root / "uploads").mkdir()
            for key, value in {"LOCAL_DATA_DIR": folder, "LLM_BACKEND": "provider", "PERSISTENT_QUERY_WORKER_ENABLED": False,
                "STARTUP_RECOVERY_ENABLED": False, "WORKSPACE_DELETE_MAINTENANCE_MODE": False,
                "RECOVERY_SINGLE_PROCESS_CONFIRMED": True}.items():
                stack.enter_context(patch.object(b.settings, key, value))
            stack.enter_context(patch.object(b.completion, "generate_response", mock))
            stack.enter_context(patch.object(b.chat, "chat_permit", permit))
            stack.enter_context(patch.object(recovery, "prepare", prepare))
            factory = f.sessionmaker(bind=engine, class_=ObservedSession, autoflush=False)
            def database():
                with factory() as db:
                    yield db
            app = f.create_app()
            app.dependency_overrides[f.get_db] = database
            @app.middleware("http")
            async def tracing(request, call_next):
                token = label.set(request.headers.get("X-Experiment-Label", "setup"))
                try:
                    return await call_next(request)
                finally:
                    label.reset(token)
            ready = threading.Event()
            class Server(f.uvicorn.Server):
                async def startup(self, sockets=None):
                    await super().startup(sockets=sockets)
                    ready.set()
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            server = Server(f.uvicorn.Config(app, workers=1, limit_concurrency=16, log_level="error", access_log=False, timeout_graceful_shutdown=5))
            thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
            def stop():
                for event in releases:
                    event.set()
                server.should_exit = True
                thread.join(30)
                assert not thread.is_alive()
            stack.callback(stop)
            thread.start()
            assert ready.wait(15)
            clients = [stack.enter_context(f.httpx.Client(base_url=f"http://127.0.0.1:{sock.getsockname()[1]}", timeout=25, trust_env=False)) for _ in range(2)]
            uid = uuid.uuid4()
            with f.Session(engine) as db:
                db.add(f.User(id=uid, email="lifecycle@example.com", password_hash="synthetic-unused"))
                db.flush()
                db.add(b.UserLLMSetting(id=str(uuid.uuid4()), user_id=uid, provider="experiment", model_name="mock",
                    base_url="https://integrate.api.nvidia.com/v1", encrypted_api_key=b.encrypt_text("synthetic-never-sent")))
                db.commit()
            token = f.create_access_token({"sub": str(uid)})
            def send(client, name, method, path, body=None):
                response = client.request(method, path, json=body, headers={"Authorization": "Bearer " + token, "X-Experiment-Label": name})
                evidence["responses"][name] = {"status": response.status_code, "body": response.text}
                return response
            with ThreadPoolExecutor(max_workers=2) as pool:
                for case in ("success", "commit_failure", "generation_failure", "delete_first"):
                    b.settings.WORKSPACE_DELETE_MAINTENANCE_MODE = False
                    for event in (*releases, llm_enter, rollback_enter, admission_enter, delete_enter):
                        event.clear()
                    wid, did = str(uuid.uuid4()), str(uuid.uuid4())
                    path = root / "uploads" / f"{wid}.txt"
                    path.write_text("Lifecycle synthetic file", encoding="utf-8")
                    with f.Session(engine) as db:
                        db.add(f.Workspace(id=wid, user_id=uid, name=case))
                        db.flush()
                        db.add(b.Document(id=did, workspace_id=wid, name=path.name, storage_key=f"uploads/{path.name}",
                            size_bytes=path.stat().st_size, status="ready", chunk_count=0, embedding_model="not-used"))
                        db.commit()
                    before = snapshot()
                    calls = evidence["llm_calls"]
                    body = {"message": QUESTION, "workspace_id": wid, "request_id": str(uuid.uuid4()), "ui_locale": "en"}
                    assert "session_id" not in body
                    chat_future = pool.submit(send, clients[0], case + "_chat", "POST", "/api/chat", body)
                    if case == "delete_first":
                        assert admission_enter.wait(10)  # Chat passed the unchanged HTTP recovery gate.
                        b.settings.WORKSPACE_DELETE_MAINTENANCE_MODE = True
                        delete_future = pool.submit(send, clients[1], case + "_delete", "DELETE", f"/api/workspaces/{wid}")
                        assert delete_enter.wait(10)
                        assert life.coordinator.snapshot()["deleting"]
                        admission_release.set()
                        assert chat_future.result(15).status_code == 409
                        assert evidence["llm_calls"] == calls and snapshot() == before and path.exists()
                        delete_release.set()
                        assert delete_future.result(15).status_code == 204
                        response = None
                    else:
                        assert llm_enter.wait(10)
                        assert life.coordinator.snapshot()["active"] == 1
                        if case == "commit_failure":
                            llm_release.set()
                            assert rollback_enter.wait(10)
                            assert b.chat.chat_slot.acquire(blocking=False), "Failure must be past chat_slot release"
                            b.chat.chat_slot.release()
                        b.settings.WORKSPACE_DELETE_MAINTENANCE_MODE = True
                        rejected = send(clients[1], case + "_blocked_delete", "DELETE", f"/api/workspaces/{wid}")
                        assert rejected.status_code == 409
                        assert snapshot() == before and path.read_text() == "Lifecycle synthetic file"
                        assert life.coordinator.snapshot()["active"] == 1
                        llm_release.set()
                        rollback_release.set()
                        response = chat_future.result(15)
                        assert response.status_code == {"success": 200, "commit_failure": 500, "generation_failure": 502}[case]
                        assert life.coordinator.snapshot()["active"] == 0
                        after_chat = snapshot()
                        if case == "success":
                            assert list(after_chat["counts"].values()) == [1, 1, 1, 2, 1, 1]
                        else:
                            assert after_chat == before
                        assert send(clients[1], case + "_delete", "DELETE", f"/api/workspaces/{wid}").status_code == 204
                    after = snapshot()
                    operation = next(op for op in after["operations"] if op["workspace_id"] == wid)
                    assert operation["state"] == "CLEANED"
                    assert operation["manifest"]["session_ids"] == ([response.json()["session_id"]] if case == "success" else [])
                    assert not path.exists() and not any(after["counts"].values())
                    assert life.coordinator.snapshot() == {"active": 0, "deleting": False, "uncertain": False}
                    assert b.chat.registry.running_count() == b.chat.registry.follower_count() == 0
                    evidence["cases"].append({"case": case, "before": before, "after": after,
                        "lifecycle": life.coordinator.snapshot(), "llm_calls": evidence["llm_calls"]-calls,
                        "manifest_session_ids": operation["manifest"]["session_ids"]})
                    print(case, "passed", flush=True)
            stop()
        evidence["temporary_directory_removed"] = not root.exists()
        evidence["source_after"] = hashes()
        assert evidence["source_before"] == evidence["source_after"]
        evidence["passed"] = True
    except BaseException:
        evidence["error"] = traceback.format_exc()
        raise
    finally:
        for event in releases:
            event.set()
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
