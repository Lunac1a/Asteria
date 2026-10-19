"""Opt-in real model + FastAPI lifespan/HTTP integration, disposable SQLite only.

Run from repo root with backend/.venv/Scripts/python.exe -B this_file --output NEW_DIR.
No external provider: LLM uses the existing explicit engineering test backend.
"""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]
import test_auth  # noqa: E402,F401 -- synthetic settings
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.knowledge import retrieval, windows_job  # noqa: E402
from app.models.knowledge import Document, Chunk  # noqa: E402

QUERY = "According to the document, what does a database transaction guarantee?"
CONTENT = "A database transaction guarantees atomicity: either all changes commit, or none are applied."


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    result = {"passed": False, "samples": [], "python": platform.python_version(),
              "platform": platform.platform(), "database": "temporary SQLite; not PostgreSQL evidence",
              "transport": "FastAPI TestClient ASGI HTTP", "llm": "explicit test backend",
              "head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()}
    handles = []
    try:
        with tempfile.TemporaryDirectory(prefix="persistent-integration-", dir=ROOT / ".local") as folder, ExitStack() as stack:
            folder = Path(folder)
            cache = ROOT / "data/mvp-test/models/models--Qdrant--paraphrase-multilingual-MiniLM-L12-v2-onnx-Q"
            shutil.copytree(cache, folder / "models" / cache.name)
            for key, value in {"LOCAL_DATA_DIR": str(folder), "EMBEDDING_BACKEND": "fastembed",
                               "PERSISTENT_QUERY_WORKER_ENABLED": True}.items():
                stack.enter_context(patch.object(settings, key, value))
            engine = create_engine("sqlite:///" + (folder / "test.db").as_posix(),
                                   connect_args={"check_same_thread": False})
            stack.callback(engine.dispose)
            Base.metadata.create_all(engine)
            factory = sessionmaker(bind=engine)
            def database():
                with factory() as db:
                    yield db
            app = create_app()
            app.dependency_overrides[get_db] = database
            started = time.perf_counter()
            with TestClient(app) as client:
                result["lifespan_startup_ms"] = (time.perf_counter()-started)*1000
                worker = app.state.query_worker
                assert worker.state == "READY"
                result["worker_pid"] = worker.pid
                result["launcher_pid"] = worker.launcher_pid
                result["job_pids"] = worker.job.pids()
                handles = [windows_job.process_handle(pid) for pid in result["job_pids"]]
                assert client.get("/api/ready").status_code == 200
                assert client.get("/api/workspaces").status_code == 401
                body = {"email": "persistent@example.com", "password": "Synthetic-Test-42"}
                registered = client.post("/api/register", json=body)
                assert registered.status_code == 200, registered.text
                token = client.post("/api/login", json=body).json()["access_token"]
                headers = {"Authorization": "Bearer " + token}
                workspace = client.post("/api/workspaces", headers=headers, json={"name": "Synthetic"}).json()["id"]
                reference = retrieval.run_worker({"texts": [QUERY]}, 30)["vectors"][0]
                worker.embed(QUERY, 30)  # Warm up both paths before alternating measured pairs.
                for pair in range(10):
                    for mode in (("disposable", "persistent") if pair % 2 == 0 else ("persistent", "disposable")):
                        start = time.perf_counter()
                        vector = (retrieval.run_worker({"texts": [QUERY]}, 30)["vectors"][0]
                                  if mode == "disposable" else worker.embed(QUERY, 30))
                        elapsed = (time.perf_counter()-start)*1000
                        difference = max(abs(a-b) for a, b in zip(reference, vector, strict=True))
                        assert difference <= 1e-6 and worker.pid == result["worker_pid"]
                        result["samples"].append({"pair": pair, "mode": mode, "ms": elapsed,
                                                  "max_abs_difference": difference})
                with factory() as db:
                    db.add(Document(id="doc", workspace_id=workspace, name="Transactions.txt",
                        storage_key="synthetic", size_bytes=len(CONTENT), status="ready",
                        embedding_model=retrieval.model_id(), chunk_count=1))
                    db.flush()
                    db.add(Chunk(id="chunk", document_id="doc", ordinal=0, content=CONTENT, vector=reference))
                    db.commit()
                def chat():
                    return client.post("/api/chat", headers=headers, json={"request_id": str(uuid.uuid4()),
                        "workspace_id": workspace, "message": QUERY, "answer_mode": "document"})
                result["http_success"] = []
                for _ in range(3):
                    response = chat()
                    assert response.status_code == 200, response.text
                    assert response.json()["answer_basis"] == "grounded"
                    result["http_success"].append({"status": response.status_code,
                        "basis": response.json()["answer_basis"], "pid": worker.pid})
                # Actual model process is killed; no fake exchange and no retry.
                windows_job.check(windows_job.terminate(worker.actual, 93))
                assert windows_job.exit_status(worker.actual, 5000)["exited"]
                start = time.perf_counter()
                response = chat()
                result["crash"] = {"status": response.status_code, "body": response.json(),
                    "ms": (time.perf_counter()-start)*1000, "worker_state": worker.state,
                    "ready_status": client.get("/api/ready").status_code,
                    "workspace_status": client.get("/api/workspaces", headers=headers).status_code}
                assert response.status_code == 503
                assert result["crash"]["ms"] < 35000
                assert result["crash"]["ready_status"] == 200
                assert result["crash"]["workspace_status"] == 200
                assert worker.state == "FAILED" and not worker.gate_held
            result["crash_lifespan_cleanup"] = [windows_job.exit_status(h, 5000) for h in handles]
            assert all(row["exited"] for row in result["crash_lifespan_cleanup"])
            for handle in handles:
                windows_job.close(handle)
            handles = []
            # A fresh app lifetime, not automatic recovery of the failed instance.
            with TestClient(create_app()) as normal_client:
                normal = normal_client.app.state.query_worker
                normal.embed(QUERY, 30)
                handles = [windows_job.process_handle(pid) for pid in normal.job.pids()]
                result["normal_shutdown_pid"] = normal.pid
                start = time.perf_counter()
            result["normal_shutdown_ms"] = (time.perf_counter()-start)*1000
            result["normal_cleanup"] = [windows_job.exit_status(h, 5000) for h in handles]
            assert all(row["exited"] for row in result["normal_cleanup"])
            assert normal.state == "STOPPED" and normal.job is None and not normal.io_thread.is_alive()
            with patch.object(settings, "PERSISTENT_QUERY_WORKER_ENABLED", False):
                default_app = create_app()
                default_app.dependency_overrides[get_db] = database
                with TestClient(default_app) as client:
                    assert default_app.state.query_worker is None
                    response = chat()
                    result["default_disposable_http_status"] = response.status_code
                    assert response.status_code == 200, response.text
            for mode in ("disposable", "persistent"):
                times = sorted(s["ms"] for s in result["samples"] if s["mode"] == mode)
                result[mode] = {"n": len(times), "mean_ms": statistics.mean(times),
                    "p50_ms": times[4], "p95_ms": times[9]}
        result["temporary_directory_removed"] = not folder.exists()
        result["passed"] = True
    finally:
        for handle in handles:
            windows_job.close(handle)
        (args.output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key not in {"samples"}}))


if __name__ == "__main__":
    main()
