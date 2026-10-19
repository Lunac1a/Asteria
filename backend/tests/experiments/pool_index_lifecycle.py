"""Real HTTP indexing with default QueuePool and deterministic local worker stub."""
import argparse
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

import workspace_creation_concurrency as f
import sqlalchemy
from sqlalchemy import event
from app.core.config import settings
from app.models.knowledge import Document, Chunk


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--pgdata", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    url = f.make_url(args.url)
    assert url.host == "127.0.0.1" and url.port == 55489
    assert url.database == "workspace_concurrency_test"
    assert args.pgdata.startswith("/tmp/asteria-concurrency-cross-")
    # No pool arguments: same create_engine defaults as production session.py.
    engine = f.create_engine(args.url, connect_args={"connect_timeout": 5,
        "options": "-cstatement_timeout=8000 -clock_timeout=4000"})
    observer = f.create_engine(args.url, poolclass=f.NullPool, connect_args={"connect_timeout": 5})
    evidence = {"head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "events": [], "runs": [], "pool_wait_time": "not measured", "passed": False}
    evidence["source_sha256"] = {p: hashlib.sha256((f.BACKEND / p).read_bytes()).hexdigest()
        for p in ("app/api/routes/knowledge.py", "app/db/session.py")}
    pool = engine.pool
    evidence["pool"] = {"class": type(pool).__name__, "size": pool.size(),
        "max_overflow": pool._max_overflow, "timeout_seconds": pool.timeout(),
        "recycle_seconds": pool._recycle, "pre_ping": pool._pre_ping,
        "reset_on_return": str(pool._reset_on_return), "sqlalchemy": sqlalchemy.__version__,
        "dialect": engine.dialect.name, "driver": engine.dialect.driver}
    active, intervals = {}, []
    mutex = threading.Lock()
    current = {"label": None, "delay": 0}
    start = time.perf_counter()
    closed, ready = threading.Event(), threading.Event()
    server = thread = sock = None

    def record(kind, **fields):
        with mutex:
            value = {"event": kind, "time": time.perf_counter() - start,
                     "run": current["label"], **fields}
            evidence["events"].append(value)
            return value["time"]

    @event.listens_for(pool, "checkout")
    def checkout(connection, connection_record, proxy):
        key = str(id(connection_record))
        timestamp = record("checkout", connection=key)
        active[key] = {"run": current["label"], "connection": key, "start": timestamp}

    @event.listens_for(pool, "checkin")
    def checkin(connection, connection_record):
        key = str(id(connection_record))
        timestamp = record("checkin", connection=key)
        interval = active.pop(key)
        intervals.append({**interval, "end": timestamp, "duration": timestamp - interval["start"]})

    content = b"Synthetic indexing lifecycle document. One deterministic chunk."

    def worker(payload, timeout):
        assert set(payload) == {"path"}
        assert Path(payload["path"]).read_bytes() == content
        assert timeout > current["delay"]
        record("worker_start", active_connections=list(active), pool_checked_out=pool.checkedout())
        # The only intentional delay; lifecycle order is measured by events.
        threading.Event().wait(current["delay"])
        record("worker_end", active_connections=list(active), pool_checked_out=pool.checkedout())
        return {"chunks": [{"page": None, "content": content.decode(), "vector": [1.0, 0.0, 0.0]}]}

    try:
        with observer.connect() as db:
            assert db.scalar(f.text("SHOW data_directory")) == args.pgdata
            assert db.scalar(f.text("SELECT current_database()")) == url.database
            assert db.scalar(f.text("SELECT count(*) FROM pg_tables WHERE schemaname='public'")) == 0
            evidence["postgres_version"] = db.scalar(f.text("SELECT version()"))
            evidence["isolation"] = db.scalar(f.text("SHOW transaction_isolation"))
        f.Base.metadata.create_all(engine)
        uid = uuid.uuid4()
        workspaces = [str(uuid.uuid4()), str(uuid.uuid4())]
        with f.Session(engine) as db:
            db.add(f.User(id=uid, email=f"pool-{uid.hex}@example.com", password_hash="synthetic-unused"))
            db.flush()
            db.add_all([f.Workspace(id=w, user_id=uid, name="pool-test") for w in workspaces])
            db.commit()
        factory = f.sessionmaker(bind=engine, autocommit=False, autoflush=False)

        def database():
            try:
                with factory() as db:
                    yield db
            finally:
                record("session_closed")
                closed.set()

        with tempfile.TemporaryDirectory() as data_dir, patch.object(settings, "LOCAL_DATA_DIR", data_dir), \
                patch("app.api.routes.knowledge.run_worker", worker):
            assert not settings.STARTUP_RECOVERY_ENABLED and not settings.WORKSPACE_DELETE_MAINTENANCE_MODE
            app = f.create_app()
            app.dependency_overrides[f.get_db] = database
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

            class Server(f.uvicorn.Server):
                async def startup(self, sockets=None):
                    await super().startup(sockets=sockets)
                    ready.set()

            server = Server(f.uvicorn.Config(app, workers=1, access_log=False, log_level="warning",
                limit_concurrency=16, timeout_graceful_shutdown=5))
            thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
            thread.start()
            assert ready.wait(10) and server.started
            token = f.create_access_token({"sub": str(uid)})
            for label, delay, workspace in zip(("A", "B"), (0, 5), workspaces):
                current.update(label=label, delay=delay)
                closed.clear()
                with f.httpx.Client(timeout=15, trust_env=False) as client:
                    begin = record("http_start")
                    response = client.post(f"http://127.0.0.1:{port}/api/workspaces/{workspace}/documents",
                        headers={"Authorization": f"Bearer {token}"},
                        files={"file": ("synthetic.txt", content, "text/plain")})
                    end = record("http_end", status=response.status_code)
                assert closed.wait(5), "Session finalizer timeout"
                result = {"run": label, "delay": delay, "http_duration": end - begin,
                          "status": response.status_code, "body": response.json()}
                evidence["runs"].append(result)
                assert response.status_code == 200 and response.json()["status"] == "ready"
                with f.Session(observer) as db:
                    doc = db.get(Document, response.json()["id"])
                    chunks = db.query(Chunk).filter_by(document_id=doc.id).all()
                    result["persisted"] = {"status": doc.status, "chunk_count": doc.chunk_count,
                        "chunks": [{"content": c.content, "vector": c.vector, "page": c.page} for c in chunks]}
                    assert doc.status == "ready" and doc.chunk_count == len(chunks) == 1
                    assert chunks[0].content == content.decode() and chunks[0].vector == [1.0, 0.0, 0.0]
                subset = [e for e in evidence["events"] if e["run"] == label]
                ws = next(e["time"] for e in subset if e["event"] == "worker_start")
                we = next(e["time"] for e in subset if e["event"] == "worker_end")
                result["worker_duration"] = we - ws
                result["checkouts"] = [i for i in intervals if i["run"] == label]
                result["checkout_total"] = sum(i["duration"] for i in result["checkouts"])
                result["checkout_max"] = max(i["duration"] for i in result["checkouts"])
                result["worker_overlap"] = [i for i in result["checkouts"] if i["start"] < we and i["end"] > ws]
                result["unreturned_connections"] = list(active)
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
        evidence["intervals"] = intervals
        args.output.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(json.dumps({"passed": evidence["passed"], "runs": evidence["runs"]}))


if __name__ == "__main__":
    main()
