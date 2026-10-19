"""Deterministic recovery evidence: real Windows Jobs/FastEmbed + isolated ASGI HTTP."""
import argparse
import concurrent.futures
from contextlib import ExitStack
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]
import test_auth  # noqa: E402,F401
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models.knowledge import Document, Chunk  # noqa: E402
from app.services.knowledge import retrieval, windows_job as native  # noqa: E402
from app.services.knowledge.persistent_query import PersistentQueryWorker  # noqa: E402

QUERY = "According to the document, what does a database transaction guarantee?"


def wait_state(supervisor, predicate):
    with supervisor.condition:
        assert supervisor.condition.wait_for(predicate, 15), supervisor.snapshot()


def rejected(supervisor):
    try:
        supervisor.embed(QUERY, 5)
    except HTTPException as exc:
        return exc.status_code
    raise AssertionError("Request unexpectedly succeeded")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    evidence = {"passed": False, "python": platform.python_version(), "platform": platform.platform(),
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "database": "temporary SQLite", "http": "FastAPI TestClient ASGI", "llm": "test backend"}
    handles = []
    original_start = PersistentQueryWorker.start
    try:
        with tempfile.TemporaryDirectory(prefix="supervisor-recovery-", dir=ROOT / ".local") as folder, ExitStack() as stack:
            root = Path(folder)
            cache = ROOT / "data/mvp-test/models/models--Qdrant--paraphrase-multilingual-MiniLM-L12-v2-onnx-Q"
            shutil.copytree(cache, root / "models" / cache.name)
            for key, value in {"LOCAL_DATA_DIR": str(root), "EMBEDDING_BACKEND": "fastembed",
                "PERSISTENT_QUERY_WORKER_ENABLED": True, "PERSISTENT_QUERY_RESTART_LIMIT": 2,
                "PERSISTENT_QUERY_FAILED_CYCLE_LIMIT": 1,
                "PERSISTENT_QUERY_RESTART_BACKOFF_SECONDS": .1}.items():
                stack.enter_context(patch.object(settings, key, value))
            engine = create_engine("sqlite:///" + (root / "test.db").as_posix(),
                                   connect_args={"check_same_thread": False})
            stack.callback(engine.dispose)
            Base.metadata.create_all(engine)
            factory = sessionmaker(bind=engine)
            def database():
                with factory() as db:
                    yield db
            def application():
                app = create_app()
                app.dependency_overrides[get_db] = database
                return app
            reference = retrieval.run_worker({"texts": [QUERY]}, 30)["vectors"][0]
            entered, release = threading.Event(), threading.Event()
            workers, replacement_checks = [], []
            def controlled_start(worker):
                workers.append(worker)
                if len(workers) == 2:
                    checks = [native.exit_status(h, 0) for h in handles]
                    replacement_checks.extend(checks)
                    assert all(c["exited"] for c in checks)
                    assert workers[0].job is None and not workers[0].gate_held
                    entered.set()
                    assert release.wait(10), "initializer checkpoint deadline"
                original_start(worker)
            with patch.object(PersistentQueryWorker, "start", controlled_start):
                with TestClient(application()) as client:
                    supervisor = client.app.state.query_worker
                    old = supervisor.worker
                    old_pids = old.job.pids()
                    handles.extend(native.process_handle(pid) for pid in old_pids)
                    body = {"email": "supervisor@example.com", "password": "Synthetic-Test-42"}
                    assert client.post("/api/register", json=body).status_code == 200
                    token = client.post("/api/login", json=body).json()["access_token"]
                    headers = {"Authorization": "Bearer " + token}
                    workspace = client.post("/api/workspaces", headers=headers, json={"name": "Synthetic"}).json()["id"]
                    with factory() as db:
                        db.add(Document(id="supervisor-doc", workspace_id=workspace, name="Transactions.txt",
                            storage_key="synthetic", size_bytes=100, status="ready",
                            embedding_model=retrieval.model_id(), chunk_count=1))
                        db.flush()
                        db.add(Chunk(id="supervisor-chunk", document_id="supervisor-doc", ordinal=0,
                            content="A database transaction guarantees atomicity: either all changes commit or none do.",
                            vector=reference))
                        db.commit()
                    def chat():
                        return client.post("/api/chat", headers=headers, json={"request_id": str(uuid.uuid4()),
                            "workspace_id": workspace, "message": QUERY, "answer_mode": "document"})
                    assert chat().status_code == 200
                    # Hold the transition lock only during the zero-work OS fault injection.
                    with supervisor.condition:
                        native.check(native.terminate(old.actual, 93))
                        assert native.exit_status(old.actual, 5000)["exited"]
                    barrier = threading.Barrier(8)
                    def request():
                        barrier.wait(5)
                        return rejected(supervisor)
                    try:
                        with concurrent.futures.ThreadPoolExecutor(8) as pool:
                            futures = [pool.submit(request) for _ in range(8)]
                            statuses = [f.result(8) for f in futures]
                        assert statuses == [503] * 8
                        assert entered.wait(8)
                        assert supervisor.snapshot()["state"] == "STARTING"
                        during = {"chat": chat().status_code, "ready": client.get("/api/ready").status_code,
                            "workspace": client.get("/api/workspaces", headers=headers).status_code,
                            "unauthenticated": client.get("/api/workspaces").status_code}
                        assert during == {"chat": 503, "ready": 200, "workspace": 200, "unauthenticated": 401}
                        assert len(workers) == 2 and supervisor.restarts == 1
                    finally:
                        release.set()
                    wait_state(supervisor, lambda: supervisor.state == "READY")
                    new = supervisor.worker
                    assert new.pid != old.pid
                    handles.extend(native.process_handle(pid) for pid in new.job.pids())
                    vector = supervisor.embed(QUERY, 10)
                    delta = max(abs(a-b) for a, b in zip(vector, reference, strict=True))
                    assert delta == 0
                    recovered = chat()
                    assert recovered.status_code == 200 and recovered.json()["answer_basis"] == "grounded"
                    evidence["single_flight"] = {"old_pid": old.pid, "new_pid": new.pid,
                        "old_pids": old_pids, "parallel_statuses": statuses, "during_start": during,
                        "replacement_boundary_exit_checks": replacement_checks, "restarts": supervisor.restarts,
                        "max_abs_difference": delta, "recovered_chat": recovered.status_code}
                evidence["normal_shutdown"] = {"state": supervisor.state, "thread_exited": not supervisor.thread.is_alive(),
                    "process_exit_checks": [native.exit_status(h, 5000) for h in handles]}
                assert all(s["exited"] for s in evidence["normal_shutdown"]["process_exit_checks"])
            # Runtime restart initialization failures use real child processes which exit 17.
            attempts = []
            failed_child = root / "failed_initializer.py"
            failed_child.write_text("raise SystemExit(17)\n", encoding="utf-8")
            launch = native.Job.launch
            def fail_launch(job, command, env):
                value = launch(job, [sys._base_executable, "-B", str(failed_child)], env)
                handles.append(native.process_handle(value[1]))
                attempts.append({"pid": value[1], "at": time.monotonic()})
                return value
            with TestClient(application()) as client:
                supervisor = client.app.state.query_worker
                with patch.object(native.Job, "launch", fail_launch):
                    with supervisor.condition:
                        native.check(native.terminate(supervisor.worker.actual, 94))
                        supervisor.condition.notify_all()
                    wait_state(supervisor, lambda: supervisor.exhausted)
                    assert supervisor.state == "MANUAL_REQUIRED" and supervisor.restarts == 2
                    assert len(attempts) == 2 and supervisor.worker.job is None
                    statuses = [rejected(supervisor) for _ in range(10)]
                    assert statuses == [503] * 10 and len(attempts) == 2
                    evidence["bounded_restarts"] = {"snapshot": supervisor.snapshot(), "attempts": attempts,
                        "statuses": statuses, "ready": client.get("/api/ready").status_code,
                        "minimum_second_backoff_seconds": .2}
            # Deterministic no-confirmation injection: suppress Job kill, leave model alive.
            with TestClient(application()) as client:
                supervisor = client.app.state.query_worker
                worker = supervisor.worker
                held_job = worker.job
                worker.shutdown_timeout = .2
                handles.extend(native.process_handle(pid) for pid in held_job.pids())
                with patch.object(worker, "_exchange", side_effect=RuntimeError("injected transport fault")), patch.object(
                        held_job, "kill", return_value=None):
                    assert rejected(supervisor) == 503
                    wait_state(supervisor, lambda: supervisor.state == "DEGRADED")
                    assert worker.gate_held and worker.job is held_job and supervisor.restarts == 0
                    assert not retrieval.worker_slot.acquire(blocking=False)
                    status = rejected(supervisor)
                    assert status == 503
                    evidence["degraded"] = {"snapshot": supervisor.snapshot(), "gate_held": worker.gate_held,
                        "job": held_job.accounting(), "actual_exit": native.exit_status(worker.actual, 0),
                        "new_request_status": status, "ready": client.get("/api/ready").status_code,
                        "injection": "transport failure plus suppressed terminate delivery; not an OS failure claim"}
                # Only application shutdown may now reclaim the retained resources. No restart.
            evidence["degraded_shutdown"] = {"state": supervisor.state, "thread_exited": not supervisor.thread.is_alive()}
            assert supervisor.state == "STOPPED"
            evidence["all_process_exit_checks"] = [native.exit_status(h, 5000) for h in handles]
            assert all(s["exited"] for s in evidence["all_process_exit_checks"])
            with patch.object(settings, "PERSISTENT_QUERY_WORKER_ENABLED", False):
                with TestClient(application()) as client:
                    assert client.app.state.query_worker is None
                    assert client.get("/api/ready").status_code == 200
                    assert chat().status_code == 200
                    evidence["default_disposable_chat"] = 200
        evidence["temporary_directory_removed"] = not root.exists()
        evidence["passed"] = True
    finally:
        for handle in handles:
            native.close(handle)
        (args.output / "result.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps(evidence))


if __name__ == "__main__":
    main()
