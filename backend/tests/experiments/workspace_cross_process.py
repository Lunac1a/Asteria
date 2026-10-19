"""Deterministic two-process experiment. Run from repo root with -B and --output DIR.

Requires WSL Ubuntu-24.04 and existing backend dependencies. Creates a fresh
dedicated PostgreSQL cluster, archives logs, then stops/removes only that cluster.
"""
import argparse
import concurrent.futures
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import queue
import socket
import subprocess
import threading
import time
import traceback
from unittest.mock import patch
import uuid

import workspace_creation_concurrency as fixture
from sqlalchemy import event
from app.core.config import settings


def make_engine(url):
    return fixture.create_engine(url, poolclass=fixture.NullPool,
        isolation_level="READ COMMITTED", connect_args={"connect_timeout": 5,
        "options": "-cstatement_timeout=8000 -clock_timeout=4000"})


def child(label, url, pgdata, ready_pipe, checkpoint, release, stop, events, counts, index, mode, dbpids):
    def record(kind, **fields):
        events.put({"time_ns": time.perf_counter_ns(), "pid": os.getpid(),
                    "request": label, "event": kind, **fields})

    engine = make_engine(url)
    try:
        with engine.connect() as db:
            assert db.scalar(fixture.text("SHOW data_directory")) == pgdata
        assert not settings.WORKSPACE_DELETE_MAINTENANCE_MODE
        assert not settings.STARTUP_RECOVERY_ENABLED

        class ObservedSession(fixture.Session):
            def commit(self):
                record("commit_start")
                try:
                    super().commit()
                    record("commit_done")
                except BaseException:
                    record("commit_error", error=traceback.format_exc())
                    raise

        @event.listens_for(engine, "after_cursor_execute")
        def executed(conn, cursor, statement, parameters, context, executemany):
            if "FOR UPDATE" in statement and mode != "race":
                record("user_lock_acquired")
                if label == "A":
                    checkpoint.set()
                    if not release.wait(20):
                        raise TimeoutError("User lock checkpoint release timeout")
            if statement.lstrip().upper().startswith("INSERT INTO WORKSPACES"):
                record("insert_executed", rowcount=cursor.rowcount)

        @event.listens_for(engine, "before_cursor_execute")
        def executing(conn, cursor, statement, parameters, context, executemany):
            if "FOR UPDATE" in statement and mode != "race":
                record("user_lock_attempt")
                if label == "B":
                    checkpoint.set()

        factory = fixture.sessionmaker(bind=engine, class_=ObservedSession,
                                      autocommit=False, autoflush=False)

        def isolated_db():
            with factory() as db:
                isolation = db.scalar(fixture.text("SHOW transaction_isolation"))
                dbpids[index] = db.scalar(fixture.text("SELECT pg_backend_pid()"))
                record("session_open", session=uuid.uuid4().hex, isolation=isolation,
                       backend_pid=dbpids[index])
                assert isolation == "read committed"
                yield db

        original_count = fixture.Query.count

        def count_hook(query):
            value = original_count(query)
            if any(d.get("entity") is fixture.Workspace for d in query.column_descriptions):
                record("count_complete", count=value)
                counts[index] = value
                if mode == "race":
                    checkpoint.set()
                    if not release.wait(20):
                        raise TimeoutError("Cross-process release timeout")
                    record("checkpoint_released")
            return value

        app = fixture.create_app()
        app.dependency_overrides[fixture.get_db] = isolated_db
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

        class Server(fixture.uvicorn.Server):
            async def startup(self, sockets=None):
                await super().startup(sockets=sockets)
                ready_pipe.send({"pid": os.getpid(), "port": port, "started": self.started})

        server = Server(fixture.uvicorn.Config(app, workers=1, access_log=False,
            log_level="warning", limit_concurrency=16, timeout_graceful_shutdown=5))

        def shutdown():
            stop.wait(60)
            server.should_exit = True

        threading.Thread(target=shutdown, daemon=True).start()
        with patch.object(fixture.Query, "count", count_hook):
            server.run(sockets=[sock])
        sock.close()
        record("api_stopped")
    except BaseException:
        record("child_error", error=traceback.format_exc())
        raise
    finally:
        engine.dispose()
        ready_pipe.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=["race", "same-user", "different-users"], default="same-user")
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    root = "/tmp/asteria-concurrency-cross-" + uuid.uuid4().hex
    pgdata = root + "/pgdata"
    url = "postgresql+psycopg://concurrency_test@127.0.0.1:55489/workspace_concurrency_test"
    repo = fixture.BACKEND.parent
    script = "/mnt/" + repo.drive[0].lower() + repo.as_posix()[2:] + "/backend/tests/experiments/workspace_concurrency_pg.sh"
    wsl = ["wsl", "-d", "Ubuntu-24.04", "--"]
    evidence = {"head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
        "cluster": root, "mode": args.mode, "events": [], "race_observed": False, "experiment_completed": False}
    evidence["source_sha256"] = {str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [repo / "backend/app/api/routes/knowledge.py", repo / "backend/app/db/session.py"]}
    ctx = mp.get_context("spawn")
    events, release, stop = ctx.Queue(), ctx.Event(), ctx.Event()
    checkpoints = [ctx.Event(), ctx.Event()]
    counts = ctx.Array("i", [-1, -1])
    dbpids = ctx.Array("i", [-1, -1])
    processes = []
    engine = None

    def record(kind, **fields):
        evidence["events"].append({"time_ns": time.perf_counter_ns(), "pid": os.getpid(),
                                   "event": kind, **fields})

    try:
        setup = subprocess.run(wsl + ["sh", script, root, "start"], capture_output=True,
                               text=True, timeout=120)
        (out / "setup.log").write_text(setup.stdout + setup.stderr, encoding="utf-8")
        setup.check_returncode()
        engine = make_engine(url)
        with engine.connect() as db:
            assert db.scalar(fixture.text("SHOW data_directory")) == pgdata
            assert db.scalar(fixture.text("SELECT current_database()")) == "workspace_concurrency_test"
            assert db.scalar(fixture.text("SELECT count(*) FROM pg_tables WHERE schemaname='public'")) == 0
            evidence["postgres_version"] = db.scalar(fixture.text("SELECT version()"))
            evidence["isolation"] = db.scalar(fixture.text("SHOW transaction_isolation"))
        fixture.Base.metadata.create_all(engine)
        uid = uuid.uuid4()
        uid_b = uuid.uuid4() if args.mode == "different-users" else uid
        with fixture.Session(engine) as db:
            db.add(fixture.User(id=uid, email=f"cross-{uid.hex}@example.com", password_hash="synthetic-unused"))
            db.flush()
            db.add_all([fixture.Workspace(user_id=uid, name=f"seed-{i}") for i in range(99)])
            db.commit()
        if uid_b != uid:
            with fixture.Session(engine) as db:
                db.add(fixture.User(id=uid_b, email=f"cross-{uid_b.hex}@example.com", password_hash="synthetic-unused"))
                db.flush()
                db.add_all([fixture.Workspace(user_id=uid_b, name=f"seed-{i}") for i in range(99)])
                db.commit()
        record("seed_committed", count=99)
        endpoints = []
        for index, label in enumerate(["A", "B"]):
            parent_pipe, child_pipe = ctx.Pipe(duplex=False)
            process = ctx.Process(target=child, args=(label, url, pgdata, child_pipe,
                checkpoints[index], release, stop, events, counts, index, args.mode, dbpids))
            process.start()
            processes.append(process)
            child_pipe.close()
            assert parent_pipe.poll(20), "API startup timeout"
            endpoint = parent_pipe.recv()
            parent_pipe.close()
            assert endpoint["started"]
            endpoints.append(endpoint)
        evidence["api_processes"] = endpoints
        assert endpoints[0]["pid"] != endpoints[1]["pid"]
        tokens = [fixture.create_access_token({"sub": str(u)}) for u in (uid, uid_b)]

        def send(index):
            label = ["A", "B"][index]
            record("http_send", request=label)
            with fixture.httpx.Client(timeout=30, trust_env=False) as client:
                response = client.post(f"http://127.0.0.1:{endpoints[index]['port']}/api/workspaces",
                    json={"name": label}, headers={"Authorization": f"Bearer {tokens[index]}"})
            record("http_response", request=label, status=response.status_code, body=response.json())
            return {"request": label, "status": response.status_code, "body": response.json()}

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(send, 0)]
            try:
                if args.mode != "race":
                    assert checkpoints[0].wait(10), "A did not acquire row lock"
                futures.append(pool.submit(send, 1))
                deadline = time.monotonic() + 15
                reached = [c.wait(max(0, deadline - time.monotonic())) for c in checkpoints]
                evidence["checkpoints_reached"] = reached
                evidence["counts_before_release"] = list(counts)
                with fixture.Session(engine) as db:
                    evidence["count_while_paused"] = db.query(fixture.Workspace).filter_by(user_id=uid).count()
                record("both_checkpoints_checked", reached=reached, counts=list(counts))
                assert all(reached), "Not both requests reached COUNT; inspect evidence"
                if args.mode == "same-user":
                    wait_deadline = time.monotonic() + 3
                    with engine.connect() as observer:
                        while True:
                            blockers = observer.scalar(fixture.text("SELECT pg_blocking_pids(:pid)"), {"pid": dbpids[1]})
                            if dbpids[0] in blockers:
                                record("B_blocked_by_A", blockers=blockers, waiter=dbpids[1])
                                break
                            assert time.monotonic() < wait_deadline, "B row-lock wait not observed"
                    assert list(counts) == [-1, -1], "COUNT executed before A released row lock"
                    assert not futures[1].done()
                elif args.mode == "different-users":
                    assert futures[1].result(timeout=8)["status"] == 200
                    assert not futures[0].done(), "A must still hold its user lock"
                    record("B_completed_while_A_held")
            finally:
                record("release_signal")
                release.set()
            evidence["responses"] = [f.result(timeout=35) for f in futures]
        with fixture.Session(engine) as db:
            rows = db.query(fixture.Workspace).filter_by(user_id=uid).all()
            evidence["final_state"] = {"count": len(rows), "new_rows": [
                {"id": r.id, "name": r.name} for r in rows if not r.name.startswith("seed-")]}
            evidence["per_user_counts"] = [db.query(fixture.Workspace).filter_by(user_id=u).count() for u in (uid, uid_b)]
        if args.mode == "same-user":
            assert [r["status"] for r in evidence["responses"]] == [200, 409]
            assert list(counts) == [99, 100]
            assert evidence["per_user_counts"] == [100, 100]
        elif args.mode == "different-users":
            assert [r["status"] for r in evidence["responses"]] == [200, 200]
            assert list(counts) == [99, 99]
            assert evidence["per_user_counts"] == [100, 100]
        evidence["final_request_counts"] = list(counts)
        record("final_database_snapshot", **evidence["final_state"])
        evidence["experiment_completed"] = True
    except BaseException:
        evidence["error"] = traceback.format_exc()
    finally:
        release.set()
        stop.set()
        evidence["process_cleanup"] = []
        for p in processes:
            p.join(12)
            forced = p.is_alive()
            if forced:
                p.terminate()
                p.join(5)
            evidence["process_cleanup"].append({"pid": p.pid, "exitcode": p.exitcode,
                "forced": forced, "alive": p.is_alive()})
        while True:
            try:
                evidence["events"].append(events.get(timeout=0.2))
            except queue.Empty:
                break
        events.close()
        events.join_thread()
        if engine:
            engine.dispose()
        # Archive BEFORE stopping, while the WSL /tmp cluster is still available.
        archive = subprocess.run(wsl + ["cat", root + "/postgres.log"], capture_output=True,
                                 text=True, timeout=10)
        (out / "postgres.log").write_text(archive.stdout + archive.stderr, encoding="utf-8")
        evidence["sql_log_archived"] = archive.returncode == 0
        cleanup_script = script.replace("workspace_concurrency_pg.sh", "workspace_cross_cleanup.sh")
        cleanup = subprocess.run(wsl + ["sh", cleanup_script, root],
                                 capture_output=True, text=True, timeout=25)
        (out / "cleanup.log").write_text(cleanup.stdout + cleanup.stderr, encoding="utf-8")
        evidence["cluster_cleanup_ok"] = cleanup.returncode == 0
        evidence["events"].sort(key=lambda e: e["time_ns"])
        evidence["race_observed"] = (evidence.get("counts_before_release") == [99, 99]
            and [r["status"] for r in evidence.get("responses", [])] == [200, 200]
            and evidence.get("final_state", {}).get("count") == 101
            and {e.get("request") for e in evidence["events"] if e["event"] == "commit_done"} == {"A", "B"})
        (out / "result.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(json.dumps({k: evidence[k] for k in ["experiment_completed", "race_observed", "cluster_cleanup_ok"]}))
    if not evidence["experiment_completed"] or not evidence["cluster_cleanup_ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
