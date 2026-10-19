"""Step 2.5 deterministic clock evidence plus real FastEmbed cooldown recovery."""
import argparse
import json
from pathlib import Path
import platform
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]
from test_restart_policy import Clock, RestartPolicyTests  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.services.knowledge import retrieval, windows_job as native  # noqa: E402
from app.services.knowledge.persistent_query import PersistentQueryWorker  # noqa: E402
from app.services.knowledge.query_supervisor import QueryWorkerSupervisor  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    evidence = {"passed": False, "platform": platform.platform(), "clock": "controlled monotonic",
                "policy_tests": [], "database": "none", "provider": "none"}
    handles = []
    try:
        for name in unittest.defaultTestLoader.getTestCaseNames(RestartPolicyTests):
            case = RestartPolicyTests(name)
            outcome = unittest.TestResult()
            case.run(outcome)
            evidence["policy_tests"].append({"name": name, "passed": outcome.wasSuccessful(),
                "events": case.evidence, "failures": [str(x) for x in outcome.failures + outcome.errors]})
            assert outcome.wasSuccessful(), evidence["policy_tests"][-1]
        with tempfile.TemporaryDirectory(prefix="restart-policy-", dir=ROOT / ".local") as folder:
            root = Path(folder)
            cache = ROOT / "data/mvp-test/models/models--Qdrant--paraphrase-multilingual-MiniLM-L12-v2-onnx-Q"
            shutil.copytree(cache, root / "models" / cache.name)
            clock, workers = Clock(), []
            def factory():
                worker = PersistentQueryWorker(settings.EMBEDDING_MODEL, root / "models",
                    retrieval.worker_slot, defer_failure_cleanup=True)
                workers.append(worker)
                if len(workers) == 2:
                    def fail():
                        worker.state = "FAILED"
                        raise RuntimeError("Injected replacement initialization failure before launch")
                    worker.start = fail
                if len(workers) == 3:
                    checks = [native.exit_status(h, 0) for h in handles]
                    evidence["replacement_exit_checks"] = checks
                    assert all(row["exited"] for row in checks), checks
                    assert all(w.job is None for w in workers[:-1])
                return worker
            s = QueryWorkerSupervisor(settings.EMBEDDING_MODEL, root / "models", retrieval.worker_slot,
                worker_factory=factory, clock=clock, restart_limit=1, window_seconds=10,
                backoff_seconds=1, failed_cycle_limit=3)
            def wait(predicate):
                with s.condition:
                    assert s.condition.wait_for(predicate, 15), s.snapshot()
            try:
                s.start()
                old_pid = s.worker.pid
                evidence["old_job_pids"] = s.worker.job.pids()
                handles.extend(native.process_handle(pid) for pid in evidence["old_job_pids"])
                evidence["old_membership"] = [s.worker.job.contains(h) for h in handles]
                query = "According to the document, what does a database transaction guarantee?"
                with patch.object(settings, "LOCAL_DATA_DIR", str(root)), patch.object(settings, "EMBEDDING_BACKEND", "fastembed"):
                    reference = retrieval.run_worker({"texts": [query]}, 30)["vectors"][0]
                with s.condition:
                    native.check(native.terminate(s.worker.actual, 93))
                    assert native.exit_status(s.worker.actual, 5000)["exited"]
                    s.condition.notify_all()
                wait(lambda: s.retry_at is not None)
                clock.advance_to(s, s.retry_at)
                wait(lambda: s.state == "COOLDOWN")
                evidence["cooldown"] = s.snapshot()
                clock.advance_to(s, s.cooldown_until)
                wait(lambda: s.retry_at is not None)
                clock.advance_to(s, s.retry_at)
                wait(lambda: s.state == "READY")
                # No request was sent between cooldown and new READY.
                assert len(workers) == 3 and s.restarts == 2
                handles.extend(native.process_handle(pid) for pid in s.worker.job.pids())
                vector = s.embed(query, 10)
                delta = max(abs(a-b) for a, b in zip(vector, reference, strict=True))
                assert delta == 0 and old_pid != s.worker.pid
                evidence["real_recovery"] = {"old_pid": old_pid, "new_pid": s.worker.pid,
                    "snapshot": s.snapshot(), "max_abs_difference": delta, "dimensions": len(vector),
                    "recovery_trigger": "background clock deadline, no embedding requests",
                    "initialization_injection": "one failure before process launch"}
            finally:
                s.close()
            evidence["exit_checks"] = [native.exit_status(h, 5000) for h in handles]
            assert all(row["exited"] for row in evidence["exit_checks"])
            evidence["supervisor_stopped"] = s.state == "STOPPED" and not s.thread.is_alive()
        evidence["temporary_directory_removed"] = not root.exists()
        evidence["passed"] = True
    finally:
        for handle in handles:
            native.close(handle)
        (args.output / "result.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps(evidence))


if __name__ == "__main__":
    main()
