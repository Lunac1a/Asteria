"""Real TCP/PostgreSQL/FastEmbed/provider-process two-session acceptance experiment."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from contextvars import ContextVar
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import socket
import tempfile
import threading
import time
import traceback
from unittest.mock import patch
import uuid

import chat_worker_ab_block as b
from app.models.learning import LearningContext
from app.models.workspace_delete_operation import WorkspaceDeleteOperation
from app.services import session_coordination as sc, workspace_lifecycle as life
from app.services.llm import provider
import provider_concurrency as resources

f, settings, native = b.f, b.settings, b.native
label = ContextVar("experiment_label", default="setup")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--load", action="store_true")
    parser.add_argument("--url", required=True)
    parser.add_argument("--pgdata", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    parsed = f.make_url(args.url)
    assert parsed.host == "127.0.0.1" and parsed.port == 55489
    assert parsed.database == "workspace_concurrency_test"
    assert args.pgdata.startswith("/tmp/asteria-concurrency-cross-")
    engine = f.create_engine(args.url, connect_args={"connect_timeout": 5,
        "options": "-cstatement_timeout=8000 -clock_timeout=4000"})
    observer = f.create_engine(args.url, poolclass=f.NullPool)
    evidence = {"passed": False, "http": [], "events": [], "batches": [], "resource_samples": []}
    guard = threading.Lock()
    entered, releases = {}, {}
    follower, rollback_entered, rollback_release = (threading.Event() for _ in range(3))
    api = api_thread = sock = None
    worker_handles, provider_handles = [], []
    provider_jobs = []

    def event(name, **fields):
        with guard:
            evidence["events"].append({"event": name, "label": label.get(), "at": time.monotonic(), **fields})

    class Mock(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            key = body["model"]
            context = json.loads(body["messages"][1]["content"])
            assert context["question"] == b.QUESTION
            assert len(context["evidence"]) == 1 and context["evidence"][0]["content"] == b.CONTENT
            event("llm_start", request=key, history=context["history"])
            entered[key].set()
            assert releases[key].wait(15)
            event("llm_work_start", request=key)
            threading.Event().wait(2)  # Same fixed Mock generation duration in both modes.
            answer = json.dumps({"answer": b.ANSWER, "citations": [1], "basis": "grounded"})
            response = json.dumps({"id": "local", "object": "chat.completion", "created": 0,
                "model": key, "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": answer}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
            event("llm_end", request=key)

    mock = ThreadingHTTPServer(("127.0.0.1", 0), Mock)
    mock_thread = threading.Thread(target=mock.serve_forever, daemon=True)
    mock_thread.start()
    url = f"http://127.0.0.1:{mock.server_port}/v1"

    class Database(f.Session):
        def commit(self):
            if label.get() == "failA":
                self.execute(f.text("SET LOCAL asteria.fail_commit = 'on'"))
            try:
                super().commit()
            except Exception:
                if label.get() == "failA":
                    self.info["failed_commit"] = True
                raise
            event("commit")

        def rollback(self):
            if self.info.pop("failed_commit", False):
                rollback_entered.set()
                assert rollback_release.wait(15)
            super().rollback()

    original_embed, original_generation = b.QueryWorkerSupervisor.embed, provider.generate_response
    original_launch, original_close = native.Job.launch, native.Job.close
    original_wait = b.chat.registry.wait

    def embed(worker, *a, **kw):
        event("embedding_start")
        try:
            result = original_embed(worker, *a, **kw)
        except Exception as exc:
            event("embedding_error", status=getattr(exc, "status_code", None), detail=str(exc))
            raise
        event("embedding_done", pid=worker.worker.pid)
        return result

    def generate(messages, key, base_url, model_name, **kw):
        # Test-only correlation tag in the mock model name; payload/history untouched.
        return original_generation(messages, key, base_url, label.get(), **kw)

    def launch(job, command, env):
        result = original_launch(job, command, env)
        if any(str(p).endswith("llm\\provider.py") or str(p).endswith("llm/provider.py") for p in command):
            provider_jobs.append(job)
            provider_handles.extend(native.process_handle(pid) for pid in job.pids())
            event("provider_launch", pid=result[1])
        return result

    def close(job):
        if job in provider_jobs and job.handle:
            event("provider_close", accounting=job.accounting())
        return original_close(job)

    def waiting(*a, **kw):
        follower.set()
        return original_wait(*a, **kw)

    try:
        with observer.connect() as db:
            assert db.scalar(f.text("SHOW data_directory")) == args.pgdata
            assert db.scalar(f.text("SELECT count(*) FROM pg_tables WHERE schemaname='public'")) == 0
            evidence["postgres"] = {"version": db.scalar(f.text("SHOW server_version")),
                                    "isolation": db.scalar(f.text("SHOW transaction_isolation"))}
        f.Base.metadata.create_all(engine)
        with tempfile.TemporaryDirectory(prefix="session-chat-", dir=f.BACKEND.parent / ".local") as folder, ExitStack() as stack:
            cache = f.BACKEND.parent / "data/mvp-test/models/models--Qdrant--paraphrase-multilingual-MiniLM-L12-v2-onnx-Q"
            shutil.copytree(cache, Path(folder) / "models" / cache.name)
            for name, value in {"LOCAL_DATA_DIR": folder, "EMBEDDING_BACKEND": "fastembed",
                "LLM_BACKEND": "provider", "PERSISTENT_QUERY_WORKER_ENABLED": True,
                "STARTUP_RECOVERY_ENABLED": False, "WORKSPACE_DELETE_MAINTENANCE_MODE": False,
                "RECOVERY_SINGLE_PROCESS_CONFIRMED": True, "SESSION_LOCK_WAIT_SECONDS": 1 if args.load else .15,
                "LLM_ALLOWED_BASE_URLS": url}.items():
                stack.enter_context(patch.object(settings, name, value))
            stack.enter_context(patch.object(settings, "SESSION_CHAT_CONCURRENCY_ENABLED", False))
            path = Path(folder) / "synthetic.txt"
            path.write_text(b.CONTENT, encoding="utf-8")
            chunks = b.retrieval.run_worker({"path": str(path)}, 90)["chunks"]
            uid, wid, did = uuid.uuid4(), str(uuid.uuid4()), str(uuid.uuid4())
            with f.Session(engine) as db:
                db.add(f.User(id=uid, email="synthetic@example.com", password_hash="unused"))
                db.flush()
                db.add(f.Workspace(id=wid, user_id=uid, name="Isolated concurrency"))
                db.add(b.UserLLMSetting(id=str(uuid.uuid4()), user_id=uid, provider="mock", base_url=url,
                    model_name="mock", encrypted_api_key=b.encrypt_text("synthetic-key")))
                db.flush()
                db.add(b.Document(id=did, workspace_id=wid, name="transactions.txt", storage_key="synthetic.txt",
                    size_bytes=len(b.CONTENT), status="ready", chunk_count=1, embedding_model=b.retrieval.model_id()))
                db.flush()
                db.add(b.Chunk(id=str(uuid.uuid4()), document_id=did, ordinal=0, **chunks[0]))
                db.commit()
            with engine.begin() as db:
                db.execute(f.text("""CREATE FUNCTION experiment_fail_commit() RETURNS trigger LANGUAGE plpgsql AS $$
                    BEGIN IF current_setting('asteria.fail_commit', true) = 'on' THEN
                    RAISE EXCEPTION 'Injected deferred COMMIT failure'; END IF; RETURN NEW; END $$"""))
                db.execute(f.text("""CREATE CONSTRAINT TRIGGER experiment_commit_guard AFTER INSERT ON chat_turns
                    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION experiment_fail_commit()"""))

            def seed():
                sid = str(uuid.uuid4())
                with f.Session(engine) as db:
                    db.add(b.ChatSession(id=sid, user_id=uid, title="Equivalent context"))
                    db.flush()
                    db.add(b.SessionWorkspace(session_id=sid, workspace_id=wid))
                    db.add(b.SessionProfile(session_id=sid, session_type="questioning"))
                    db.add(LearningContext(session_id=sid, notes="isolated:" + sid))
                    for i, msg in enumerate(b.HISTORY):
                        db.add(b.Message(id=str(uuid.uuid4()), session_id=sid,
                            created_at=datetime(2020, 1, 1, tzinfo=timezone.utc)+timedelta(seconds=i), **msg))
                    db.commit()
                return sid

            factory = f.sessionmaker(bind=engine, class_=Database)
            def database():
                with factory() as db:
                    yield db
            for target, name, value in ((b.app_main, "engine", engine),
                (b.QueryWorkerSupervisor, "embed", embed), (b.completion, "generate_response", generate),
                (native.Job, "launch", launch), (native.Job, "close", close),
                (b.chat.registry, "wait", waiting)):
                stack.enter_context(patch.object(target, name, value))
            app = f.create_app()
            app.dependency_overrides[f.get_db] = database
            @app.middleware("http")
            async def tracing(request, call_next):
                token = label.set(request.headers.get("X-Experiment", "setup"))
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
            api = Server(f.uvicorn.Config(app, workers=1, limit_concurrency=16,
                access_log=False, log_level="error", timeout_graceful_shutdown=5))
            api_thread = threading.Thread(target=api.run, kwargs={"sockets": [sock]}, daemon=True)
            def shutdown():
                for e in releases.values():
                    e.set()
                rollback_release.set()
                api.should_exit = True
                api_thread.join(20)
                assert not api_thread.is_alive()
            stack.callback(shutdown)
            api_thread.start()
            assert ready.wait(45)
            worker_handles = [native.process_handle(p) for p in app.state.query_worker.worker.job.pids()]
            evidence["model_pid"] = app.state.query_worker.worker.pid
            headers = {"Authorization": "Bearer " + f.create_access_token({"sub": str(uid)})}
            base = f"http://127.0.0.1:{sock.getsockname()[1]}"
            def send(key, body=None, method="POST", route="/api/chat"):
                start = time.monotonic()
                with f.httpx.Client(base_url=base, timeout=25, trust_env=False) as client, ExitStack() as request_scope:
                    if method == "DELETE" and route == f"/api/workspaces/{wid}":
                        request_scope.enter_context(patch.object(settings, "WORKSPACE_DELETE_MAINTENANCE_MODE", True))
                    r = client.request(method, route, json=body, headers={**headers, "X-Experiment": key})
                evidence["http"].append({"label": key, "start": start, "end": time.monotonic(),
                    "status": r.status_code, "body": r.json() if r.status_code != 204 and r.text.startswith("{") else r.text})
                return r
            assert send("ready", method="GET", route="/api/ready").status_code == 200
            with f.httpx.Client(base_url=base, trust_env=False) as client:
                assert client.get("/api/workspaces", follow_redirects=True).status_code in (401, 403)

            def payload(sid):
                return {"workspace_id": wid, "session_id": sid, "message": b.QUESTION,
                        "request_id": str(uuid.uuid4()), "answer_mode": "document", "ui_locale": "en"}
            def prepare(key):
                entered[key], releases[key] = threading.Event(), threading.Event()
            def verify(sid, body, success):
                with f.Session(observer) as db:
                    turn = db.get(b.ChatTurn, body["request_id"])
                    rows = db.query(b.Message).filter_by(session_id=sid).all()
                    assert (turn is not None) == success and len(rows) == (4 if success else 2)
                    ctx = db.get(LearningContext, sid)
                    assert ctx.notes == "isolated:" + sid and ctx.revision == 1
                    if success:
                        assert turn.session_id == sid and turn.response["answer"] == b.ANSWER
                        ev = [db.get(b.MessageEvidence, m.id) for m in rows if m.content == b.ANSWER]
                        assert len(ev) == 1 and ev[0].sources[0]["document_id"] == did
                    assert db.query(WorkspaceDeleteOperation).count() == 0
                event("integrity", sid=sid, success=success)
            def resource_sample():
                rows = []
                for job in list(provider_jobs):
                    if not job.handle:
                        continue
                    for pid in job.pids():
                        handle = native.check(native.open_process(0x101011, False, pid))
                        provider_handles.append(handle)
                        row = resources.f.snapshot({pid})[0]
                        row.update(resources.metrics(handle))
                        rows.append(row)
                evidence["resource_samples"].append({"at": time.monotonic(), "providers": rows,
                    "api": b.rss(__import__('os').getpid()),
                    "embedding": [b.rss(p) for p in app.state.query_worker.worker.job.pids()]})

            with ThreadPoolExecutor(max_workers=3) as pool:
                prepare("warmup")
                releases["warmup"].set()
                assert send("warmup", payload(seed())).status_code == 200
                if args.load:
                    from final_load_workload import run
                    run({**globals(), **locals()})
                    settings.SESSION_LOCK_WAIT_SECONDS = .15  # Separate fault regressions only.
                else:
                    for index, enabled in enumerate((False, True, True, False, False, True)):
                        settings.SESSION_CHAT_CONCURRENCY_ENABLED = enabled
                        sa, sb = seed(), seed()
                        a, ckey = f"batch{index}A", f"batch{index}B"
                        prepare(a)
                        prepare(ckey)
                        pa, pb = payload(sa), payload(sb)
                        batch_start = time.monotonic()
                        fa = pool.submit(send, a, pa)
                        assert entered[a].wait(12)
                        assert sa in sc.coordinator.snapshot() and not fa.done()
                        fb = pool.submit(send, ckey, pb)
                        if enabled:
                            assert entered[ckey].wait(12)
                            assert not fa.done() and set((sa, sb)) <= sc.coordinator.snapshot().keys()
                            assert b.chat.admission.snapshot()["shared"] == 2
                        else:
                            assert fb.result(5).status_code == 429
                        resource_sample()
                        releases[a].set()
                        releases[ckey].set()
                        ra, rb = fa.result(15), fb.result(15)
                        assert (ra.status_code, rb.status_code) == ((200, 200) if enabled else (200, 429))
                        verify(sa, pa, True)
                        verify(sb, pb, enabled)
                        evidence["batches"].append({"index": index, "enabled": enabled,
                            "labels": [a, ckey], "seconds": time.monotonic()-batch_start,
                            "statuses": [ra.status_code, rb.status_code]})
                settings.SESSION_CHAT_CONCURRENCY_ENABLED = True
                # Same-session, legacy new-session and Session Delete exclusion.
                sid = seed()
                prepare("held")
                body = payload(sid)
                owner = pool.submit(send, "held", body)
                assert entered["held"].wait(12)
                assert send("same", payload(sid)).status_code == 409
                assert send("new-denied", payload(None)).status_code == 429
                assert send("delete-session", method="DELETE", route=f"/api/chat/sessions/{sid}").status_code == 409
                assert send("delete-workspace", method="DELETE", route=f"/api/workspaces/{wid}").status_code == 409
                waiter = pool.submit(send, "same-id", body)
                assert follower.wait(5)
                releases["held"].set()
                one, two = owner.result(15), waiter.result(15)
                assert one.status_code == two.status_code == 200 and one.json() == two.json()
                assert sum(e["event"] == "llm_start" and e.get("request") == "held" for e in evidence["events"]) == 1
                verify(sid, body, True)
                # Deferred COMMIT failure: global admission releases, permit/session do not.
                sid = seed()
                prepare("failA")
                failed_body = payload(sid)
                task = pool.submit(send, "failA", failed_body)
                assert entered["failA"].wait(12)
                releases["failA"].set()
                assert rollback_entered.wait(12)
                assert sid in sc.coordinator.snapshot() and life.coordinator.snapshot()["active"] > 0
                assert send("failed-same", payload(sid)).status_code == 409
                assert send("failed-delete", method="DELETE", route=f"/api/workspaces/{wid}").status_code == 409
                rollback_release.set()
                assert task.result(10).status_code == 500
                verify(sid, failed_body, False)
                # New-session still owns the exclusive legacy slot; concurrent existing denied.
                prepare("new-owner")
                newbody = payload(None)
                task = pool.submit(send, "new-owner", newbody)
                assert entered["new-owner"].wait(12)
                assert send("existing-denied", payload(seed())).status_code == 429
                releases["new-owner"].set()
                assert task.result(12).status_code == 200
            assert sc.coordinator.snapshot() == {} and life.coordinator.snapshot()["active"] == 0
            assert b.chat.registry.running_count() == b.chat.registry.follower_count() == 0
            assert b.chat.admission.snapshot()["shared"] == 0
            assert send("final-ready", method="GET", route="/api/ready").status_code == 200
            evidence["supervisor"] = app.state.query_worker.snapshot()
            shutdown()
            evidence["worker_exits"] = [native.exit_status(h, 2000) for h in worker_handles]
            evidence["provider_exits"] = [native.exit_status(h, 2000) for h in provider_handles]
            assert all(e["exited"] for e in evidence["worker_exits"] + evidence["provider_exits"])
            evidence["passed"] = True
    except BaseException:
        evidence["error"] = traceback.format_exc()
        raise
    finally:
        for e in releases.values():
            e.set()
        rollback_release.set()
        if api:
            api.should_exit = True
        if api_thread:
            api_thread.join(20)
            evidence["api_stopped"] = not api_thread.is_alive()
        if sock:
            sock.close()
        for h in worker_handles + provider_handles:
            native.close(h)
        mock.shutdown()
        mock.server_close()
        mock_thread.join(2)
        engine.dispose()
        observer.dispose()
        (args.output / "result.json").write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
