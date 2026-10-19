"""Real TCP/PostgreSQL session coordination; synthetic local model backend only."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from contextvars import ContextVar
import json
from pathlib import Path
import socket
import subprocess
import threading
import traceback
from unittest.mock import patch
import os

import chat_baseline as b
from test_learning import LearningAPITests
from app.services import session_coordination as sessions, workspace_lifecycle as life
from app.models.learning import LearningContext

f = b.f
label = ContextVar("session_experiment_label", default="setup")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--pgdata", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--concurrent", action="store_true")
    args = parser.parse_args()
    url = f.make_url(args.url)
    assert url.drivername == "postgresql+psycopg" and url.host == "127.0.0.1" and url.port == 55489
    assert url.database == "workspace_concurrency_test" and args.pgdata.startswith("/tmp/asteria-concurrency-cross-")
    args.output.mkdir(parents=True, exist_ok=False)
    evidence = {"passed": False, "cases": [], "responses": {}, "transport": "real TCP, one Uvicorn worker",
                "models": "existing deterministic test backends, no paid provider",
                "head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()}
    observer = f.create_engine(args.url, poolclass=f.NullPool)
    with observer.connect() as db:
        assert db.scalar(f.text("SHOW data_directory")) == args.pgdata
        version = db.scalar(f.text("SHOW server_version"))
        assert version.startswith("16.")
        evidence["postgres"] = {"version": version, "isolation": db.scalar(f.text("SHOW transaction_isolation"))}
    observer.dispose()
    fixture = LearningAPITests(methodName="runTest")
    entered, release, attempting = threading.Event(), threading.Event(), threading.Event()
    server = thread = sock = None
    mode = None
    failed = set()
    parallel_entered = {key: threading.Event() for key in ("parallel_a", "parallel_b")}
    parallel_release = threading.Event()
    original_answer, original_commit, original_rollback = b.chat.grounded_answer, f.Session.commit, f.Session.rollback
    def answer(*a, **kw):
        if label.get() in parallel_entered:
            parallel_entered[label.get()].set()
            assert parallel_release.wait(10)
        if label.get() == "chat_owner":
            entered.set()
            assert release.wait(10)
        return original_answer(*a, **kw)
    def commit(db):
        if label.get() == "learning_owner":
            entered.set()
            assert release.wait(10)
        if label.get() == "failed_owner":
            db.execute(f.text("SET LOCAL asteria.fail_commit = 'on'"))
            try:
                original_commit(db)
            except Exception:
                failed.add(id(db))
                raise
        else:
            original_commit(db)
    def rollback(db):
        if id(db) in failed:
            entered.set()
            assert release.wait(10)
            failed.remove(id(db))
        original_rollback(db)
    class ObservedLock:
        def __init__(self, lock):
            self.lock = lock
        def acquire(self, **kwargs):
            attempting.set()  # reference already registered; owner still paused
            return self.lock.acquire(**kwargs)
        def release(self):
            return self.lock.release()
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {"ASTERIA_TEST_DATABASE_URL": args.url}))
            fixture.setUp()
            stack.callback(fixture.doCleanups)
            stack.enter_context(patch.object(b.settings, "SESSION_LOCK_WAIT_SECONDS", 5))
            stack.enter_context(patch.object(b.settings, "SESSION_CHAT_CONCURRENCY_ENABLED", args.concurrent))
            app = f.create_app()
            app.dependency_overrides.update(fixture.client.app.dependency_overrides)
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
            server = Server(f.uvicorn.Config(app, workers=1, limit_concurrency=16, access_log=False, log_level="error", timeout_graceful_shutdown=5))
            thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
            def stop():
                release.set()
                server.should_exit = True
                thread.join(20)
                assert not thread.is_alive()
            stack.callback(stop)
            thread.start()
            assert ready.wait(10)
            clients = [stack.enter_context(f.httpx.Client(base_url=f"http://127.0.0.1:{sock.getsockname()[1]}", timeout=20, trust_env=False)) for _ in range(2)]
            def send(i, name, method, path, data=None):
                result = clients[i].request(method, path, json=data, headers={**fixture.a, "X-Experiment-Label": name})
                evidence["responses"][str(mode) + ":" + name] = {"status": result.status_code, "body": result.text}
                return result
            def create():
                response = send(0, "setup", "POST", "/api/chat", {"message": "Give a general explanation of atomicity.",
                    "workspace_id": fixture.workspace, "session_type": "learning"})
                assert response.status_code == 200, response.text
                sid = response.json()["session_id"]
                ctx = send(0, "read", "GET", f"/api/chat/sessions/{sid}/learning").json()
                return sid, {"revision": ctx["revision"], "goal": "Atomicity", "focus": "focus", "notes": "controlled notes", "next_step": "", "evidence": []}
            with fixture.engine.begin() as db:
                db.execute(f.text("""CREATE FUNCTION experiment_fail_commit() RETURNS trigger LANGUAGE plpgsql AS $$
                    BEGIN IF current_setting('asteria.fail_commit', true) = 'on' THEN
                    RAISE EXCEPTION 'Injected deferred COMMIT failure'; END IF; RETURN NEW; END $$"""))
                db.execute(f.text("""CREATE CONSTRAINT TRIGGER experiment_commit_guard AFTER INSERT ON chat_turns
                    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION experiment_fail_commit()"""))
            stack.enter_context(patch.object(b.chat, "grounded_answer", answer))
            stack.enter_context(patch.object(f.Session, "commit", commit))
            stack.enter_context(patch.object(f.Session, "rollback", rollback))
            with ThreadPoolExecutor(max_workers=2) as pool:
                if args.concurrent:
                    mode = "parallel_learning_sessions"
                    first, first_notes = create()
                    second, second_notes = create()
                    with fixture.factory() as db:
                        db.get(LearningContext, first).notes = "first-only"
                        db.get(LearningContext, second).notes = "second-only"
                        db.commit()
                    def body(sid):
                        return {"message": "Give a general explanation of atomicity.",
                                "workspace_id": fixture.workspace, "session_id": sid}
                    fa = pool.submit(send, 0, "parallel_a", "POST", "/api/chat", body(first))
                    try:
                        assert parallel_entered["parallel_a"].wait(10)
                        fb = pool.submit(send, 1, "parallel_b", "POST", "/api/chat", body(second))
                        assert parallel_entered["parallel_b"].wait(10)
                        assert set((first, second)) <= sessions.coordinator.snapshot().keys()
                        assert b.chat.admission.snapshot()["shared"] == 2
                    finally:
                        parallel_release.set()
                    assert fa.result(10).status_code == fb.result(10).status_code == 200
                    with fixture.factory() as db:
                        for sid, before, notes in ((first, first_notes, "first-only"), (second, second_notes, "second-only")):
                            ctx = db.get(LearningContext, sid)
                            assert ctx.revision == before["revision"] + 1 and ctx.notes == notes
                            assert db.query(b.ChatTurn).filter_by(session_id=sid).count() == 2
                            assert db.query(b.Message).filter_by(session_id=sid).count() == 4
                    evidence["cases"].append({"case": mode, "statuses": [200, 200],
                        "independent_learning_revisions_and_notes": True})
                isolated = sessions.Coordinator()
                with isolated.hold("first"):
                    def independent():
                        with isolated.hold("second", .1):
                            return True
                    assert pool.submit(independent).result(2)
                assert isolated.snapshot() == {}
                evidence["different_session_coordinator"] = {"independent_acquisition": True, "registry_after": {}}
                for mode in ("chat_then_learning", "learning_then_chat", "chat_then_delete", "rollback_timeout"):
                    sid, notes = create()
                    for event in (entered, release, attempting):
                        event.clear()
                    path = f"/api/chat/sessions/{sid}"
                    body = {"message": "Give a general explanation of atomicity.", "workspace_id": fixture.workspace, "session_id": sid}
                    if mode == "learning_then_chat":
                        owner = pool.submit(send, 0, "learning_owner", "PUT", path + "/learning/notes", notes)
                    else:
                        owner = pool.submit(send, 0, "failed_owner" if mode == "rollback_timeout" else "chat_owner", "POST", "/api/chat", body)
                    assert entered.wait(10)
                    assert sid in sessions.coordinator.snapshot()
                    entry = sessions.coordinator._entries[sid]
                    entry.lock = ObservedLock(entry.lock)
                    if mode == "chat_then_learning":
                        waiter = pool.submit(send, 1, "waiter", "PUT", path + "/learning/notes", notes)
                    elif mode == "learning_then_chat":
                        waiter = pool.submit(send, 1, "waiter", "POST", "/api/chat", body)
                    else:
                        if mode == "rollback_timeout":
                            b.settings.SESSION_LOCK_WAIT_SECONDS = .05
                            assert b.chat.chat_slot.acquire(blocking=False)
                            b.chat.chat_slot.release()
                        waiter = pool.submit(send, 1, "waiter", "DELETE", path)
                    assert attempting.wait(5)
                    before = sessions.coordinator.snapshot()
                    if mode == "rollback_timeout":
                        assert waiter.result(5).status_code == 409
                        assert sessions.coordinator.snapshot() == {sid: 1}
                        assert life.coordinator.snapshot()["active"] == 1
                        b.settings.WORKSPACE_DELETE_MAINTENANCE_MODE = True
                        b.settings.RECOVERY_SINGLE_PROCESS_CONFIRMED = True
                        denied = send(1, "workspace_delete", "DELETE", f"/api/workspaces/{fixture.workspace}")
                        assert denied.status_code == 409
                        b.settings.WORKSPACE_DELETE_MAINTENANCE_MODE = False
                    else:
                        assert before[sid] == 2 and not waiter.done()
                    release.set()
                    owner_result, waiter_result = owner.result(10), waiter.result(10)
                    expected = {"chat_then_learning": (200, 409), "learning_then_chat": (200, 200),
                                "chat_then_delete": (200, 204), "rollback_timeout": (500, 409)}[mode]
                    assert (owner_result.status_code, waiter_result.status_code) == expected
                    assert sessions.coordinator.snapshot() == {} and life.coordinator.snapshot()["active"] == 0
                    with fixture.factory() as db:
                        ctx = db.get(LearningContext, sid)
                        turns = db.query(b.ChatTurn).filter_by(session_id=sid).count()
                        messages = db.query(b.Message).filter_by(session_id=sid).count()
                        if mode == "chat_then_delete":
                            assert ctx is None and turns == messages == 0 and db.get(b.ChatSession, sid) is None
                        else:
                            assert turns == (1 if mode == "rollback_timeout" else 2) and messages == 2 * turns
                            assert ctx.revision == notes["revision"] + {"chat_then_learning": 1, "learning_then_chat": 2, "rollback_timeout": 0}[mode]
                            if mode == "learning_then_chat":
                                assert ctx.notes == "controlled notes"
                    evidence["cases"].append({"case": mode, "statuses": expected, "waiting_references": before,
                        "registry_after": sessions.coordinator.snapshot(), "lifecycle_after": life.coordinator.snapshot(),
                        "turns": turns, "messages": messages})
                    b.settings.SESSION_LOCK_WAIT_SECONDS = 5
                    print(mode, "passed", flush=True)
            stop()
        evidence["passed"] = True
    except BaseException:
        evidence["error"] = traceback.format_exc()
        raise
    finally:
        parallel_release.set()
        release.set()
        if server:
            server.should_exit = True
        if thread:
            thread.join(20)
            evidence["api_stopped"] = not thread.is_alive()
        if sock:
            sock.close()
        evidence["session_registry"] = sessions.coordinator.snapshot()
        evidence["lifecycle"] = life.coordinator.snapshot()
        (args.output / "result.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
