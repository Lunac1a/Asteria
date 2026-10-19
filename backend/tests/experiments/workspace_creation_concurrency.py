"""Opt-in real HTTP / PostgreSQL experiment; never loads the development DB.

Run with backend/.venv/Scripts/python.exe -B this_file --url URL
--pgdata EXACT_ISOLATED_DATA_DIRECTORY --output EVIDENCE_JSON.
Requires a fresh database named workspace_concurrency_test in a dedicated cluster.
Leaves synthetic rows and evidence intact. No production code is patched.
"""
import argparse
import concurrent.futures
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import traceback
from unittest.mock import patch
import uuid

BACKEND = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(BACKEND), str(BACKEND / "tests")]
# Existing complete synthetic settings bootstrap, before any application import.
import test_auth  # noqa: E402,F401 -- synthetic settings import side effects
import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import Request  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import Query, Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402
from app.main import create_app  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.models.users import User  # noqa: E402
from app.models.knowledge import Workspace  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--pgdata", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    parsed = make_url(args.url)
    assert parsed.drivername == "postgresql+psycopg"
    assert parsed.host == "127.0.0.1"
    assert parsed.database == "workspace_concurrency_test"
    assert parsed.port != 55432, "Development PostgreSQL port forbidden"
    assert args.pgdata.startswith("/tmp/asteria-concurrency-")
    evidence = {"head": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=BACKEND, text=True).strip(),
        "events": [], "passed": False, "workers": 1}
    lock = threading.Lock()
    start = time.monotonic()

    def record(event, **fields):
        with lock:
            evidence["events"].append({"sequence": len(evidence["events"]) + 1,
                "elapsed_seconds": round(time.monotonic() - start, 6),
                "event": event, **fields})

    engine = create_engine(args.url, poolclass=NullPool, connect_args={
        "connect_timeout": 5,
        "options": "-cstatement_timeout=8000 -clock_timeout=4000"})
    counted, release, ready = threading.Event(), threading.Event(), threading.Event()
    server = None
    thread = None
    sock = None
    try:
        # Read-only identity and emptiness checks MUST precede all DDL/DML.
        with engine.connect() as db:
            assert db.scalar(text("SHOW data_directory")) == args.pgdata
            assert db.scalar(text("SELECT current_database()")) == parsed.database
            assert db.scalar(text("SELECT count(*) FROM pg_tables WHERE schemaname='public'")) == 0
            evidence["database"] = {"data_directory": args.pgdata,
                "name": parsed.database, "port": parsed.port,
                "version": db.scalar(text("SELECT version()")),
                "default_isolation": db.scalar(text("SHOW default_transaction_isolation"))}
        Base.metadata.create_all(engine)
        uid = uuid.uuid4()
        with Session(engine) as db:
            db.add(User(id=uid, email=f"concurrency-{uid.hex}@example.com", password_hash="synthetic-unused"))
            db.flush()
            db.add_all([Workspace(user_id=uid, name=f"seed-{i}") for i in range(99)])
            db.commit()
        record("seed_committed", count=99)

        class ObservedSession(Session):
            def commit(self):
                record("commit_start", request=self.info.get("request"))
                super().commit()
                record("commit_done", request=self.info.get("request"))

        factory = sessionmaker(bind=engine, class_=ObservedSession,
                               autocommit=False, autoflush=False)
        session_ids = []

        def isolated_db(request: Request):
            with factory() as db:
                label = request.headers.get("x-experiment-request", "unknown")
                sid = uuid.uuid4().hex
                session_ids.append(sid)
                db.info["request"] = label
                record("session_open", request=label, session=sid,
                       backend_pid=db.scalar(text("SELECT pg_backend_pid()")),
                       isolation=db.scalar(text("SHOW transaction_isolation")))
                try:
                    yield db
                finally:
                    record("session_dependency_exit", request=label, session=sid)

        original_count = Query.count

        def count_checkpoint(query):
            result = original_count(query)
            if any(d.get("entity") is Workspace for d in query.column_descriptions):
                label = query.session.info.get("request")
                record("workspace_count_returned", request=label, count=result)
                if label == "A":
                    counted.set()
                    if not release.wait(12):
                        raise TimeoutError("A checkpoint release exceeded 12 seconds")
                    record("A_released")
            return result

        app = create_app()
        app.dependency_overrides[get_db] = isolated_db

        class ReadyServer(uvicorn.Server):
            async def startup(self, sockets=None):
                await super().startup(sockets=sockets)
                ready.set()

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = ReadyServer(uvicorn.Config(app, host="127.0.0.1", port=port,
            workers=1, access_log=False, log_level="warning", limit_concurrency=16,
            timeout_keep_alive=5, timeout_graceful_shutdown=5))
        token = create_access_token({"sub": str(uid)})

        def send(label):
            record("http_send", request=label)
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=8, trust_env=False) as client:
                response = client.post("/api/workspaces", json={"name": label}, headers={
                    "Authorization": f"Bearer {token}", "x-experiment-request": label})
            record("http_response", request=label, status=response.status_code, body=response.json())
            return response

        def snapshot(label):
            with Session(engine) as db:
                rows = db.query(Workspace).filter_by(user_id=uid).all()
                state = {"count": len(rows), "created": sorted(r.name for r in rows if not r.name.startswith("seed-"))}
                record("database_snapshot", label=label, **state)
                return state

        with patch.object(Query, "count", count_checkpoint):
            thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
            thread.start()
            assert ready.wait(10), "Uvicorn startup timeout"
            assert server.started, "Uvicorn did not start"
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                a = pool.submit(send, "A")
                try:
                    assert counted.wait(8), "A did not reach COUNT checkpoint"
                    b = send("B-held")
                    assert b.status_code == 429, b.text
                    assert not a.done(), "A was not held"
                    assert snapshot("A_held")["count"] == 99
                finally:
                    record("release_signal")
                    release.set()
                response_a = a.result(timeout=10)
                assert response_a.status_code == 200, response_a.text
            assert snapshot("after_A") == {"count": 100, "created": ["A"]}
            retry = send("B-retry")
            assert retry.status_code == 409, retry.text
            evidence["final_state"] = snapshot("after_B_retry")
            assert evidence["final_state"] == {"count": 100, "created": ["A"]}
            assert len(session_ids) == len(set(session_ids)) == 3
            counts = [(e.get("request"), e.get("count")) for e in evidence["events"]
                      if e["event"] == "workspace_count_returned"]
            assert counts == [("A", 99), ("B-retry", 100)], counts
            evidence["passed"] = True
    except BaseException:
        evidence["error"] = traceback.format_exc()
        raise
    finally:
        release.set()
        if server:
            server.should_exit = True
        if thread:
            thread.join(10)
            evidence["api_stopped"] = not thread.is_alive()
        if sock:
            sock.close()
        engine.dispose()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(json.dumps({"passed": evidence["passed"], "evidence": str(args.output)}))


if __name__ == "__main__":
    main()
