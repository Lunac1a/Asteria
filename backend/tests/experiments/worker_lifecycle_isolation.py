"""Windows Job ownership experiment. No application, database or provider imports."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid

from windows_worker_job import Job, check, close, exit_status, process_handle, terminate

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SOURCE = ROOT / "backend/app/services/knowledge/index_worker.py"
MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
QUERY = "According to the document, what does a database transaction guarantee?"


def emit(value):
    print(json.dumps(value), flush=True)


class Messages:
    def __init__(self, stream):
        self.queue = queue.Queue()

        def read():
            try:
                for line in stream:
                    self.queue.put(json.loads(line))
            except BaseException as exc:
                self.queue.put({"reader_error": repr(exc)})
            finally:
                self.queue.put(None)

        self.thread = threading.Thread(target=read, daemon=True)
        self.thread.start()

    def get(self, timeout=10):
        try:
            value = self.queue.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError("Protocol deadline") from None
        if value is None or "reader_error" in value:
            raise RuntimeError(f"Protocol closed: {value}")
        return value


class Slot:
    """One test-only ownership slot, not the production document_slot."""
    def __init__(self):
        self.held = True
        self.state = "RUNNING"

    def confirm(self, statuses, accounting):
        confirmed = all(s["exited"] for s in statuses) and accounting["active_processes"] == 0
        self.held = not confirmed
        self.state = "STOPPED" if confirmed else "DEGRADED"
        return {"state": self.state, "slot_held": self.held, "admission_allowed": not self.held}


def supervisor(args):
    job = Job()
    handles = []
    stdin = stdout = messages = None
    try:
        env = {k: v for k, v in os.environ.items() if k.upper() in {
            "SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA"}}
        env.update(OMP_NUM_THREADS="2", TOKENIZERS_PARALLELISM="false", HF_HUB_OFFLINE="1")
        command = [str(ROOT / "backend/.venv/Scripts/python.exe"), "-B",
                   str(HERE / "persistent_embedding_child.py"), "--source", str(SOURCE),
                   "--model", MODEL, "--cache", str(args.cache)]
        launcher, launcher_pid, stdin, stdout = job.launch(command, env, args.output / "worker.stderr.log")
        handles.append(launcher)
        messages = Messages(stdout)
        ready = messages.get(40)
        assert ready["type"] == "ready"
        actual = process_handle(ready["pid"])
        handles.append(actual)
        assert job.contains(actual)

        def send(**payload):
            task_id = str(uuid.uuid4())
            stdin.write(json.dumps({"task_id": task_id, **payload}) + "\n")
            stdin.flush()
            return task_id

        task_id = send(texts=[QUERY])
        result = messages.get()
        assert result["type"] == "result" and result["task_id"] == task_id
        vector = result["vectors"][0]
        assert len(vector) == 384 and all(math.isfinite(v) for v in vector)
        slot = Slot()
        job_pids = job.pids()
        descendant_handles = [process_handle(pid) for pid in job_pids
                              if pid not in (launcher_pid, ready["pid"])]
        handles.extend(descendant_handles)
        emit({"type": "ready", "supervisor_pid": os.getpid(), "launcher_pid": launcher_pid,
              "actual_pid": ready["pid"], "membership": {"launcher": job.contains(launcher),
              "actual": job.contains(actual)}, "job_accounting": job.accounting(),
              "job_pids": job_pids,
              "worker_ready": ready, "normal_result": {"task_id": task_id, "vector": vector}})
        assert Messages(sys.stdin).get(15)["command"] == "proceed"
        if args.case != "normal":
            task_id = send(op="hold")
            assert messages.get(5) == {"type": "accepted", "task_id": task_id}
            emit({"type": "held", "task_id": task_id, "actual_pid": ready["pid"]})
        if args.case == "parent_kill":
            # Observer kills actual supervisor at this explicit checkpoint; no finally runs.
            threading.Event().wait(20)
            raise TimeoutError("Observer failed to terminate supervisor")

        report = {"type": "complete", "case": args.case}
        start = time.perf_counter()
        if args.case == "normal":
            task_id = send(op="stop")
            assert messages.get(5) == {"type": "stopped", "task_id": task_id}
            primary_statuses = [exit_status(h, 5000) for h in handles[:2]]
            report["after_graceful"] = {"primary_statuses": primary_statuses,
                "job": job.accounting(), "remaining_pids": job.pids()}
            # Normal shutdown includes the owned process tree, not merely model + launcher.
            if job.accounting()["active_processes"]:
                report["residual_job_termination"] = True
                job.kill()
        else:
            if args.case == "hang":
                try:
                    messages.get(.2)
                    raise AssertionError("Held worker unexpectedly completed")
                except TimeoutError:
                    report["response_deadline_ms"] = 200
            else:
                # Fault injection: termination adapter does not deliver kill. Real OS waits
                # must time out on a live held process; this is NOT an OS kill failure claim.
                report["injection"] = "termination delivery suppressed by test adapter"
                timeout_start = time.perf_counter()
                statuses = [exit_status(h, 100) for h in handles]
                report["unconfirmed"] = {"statuses": statuses, "job": job.accounting(),
                    "elapsed_ms": (time.perf_counter()-timeout_start)*1000,
                    **slot.confirm(statuses, job.accounting())}
                assert all(s["wait_result"] == 258 for s in statuses)
                assert slot.state == "DEGRADED" and slot.held
                assert not report["unconfirmed"]["admission_allowed"]
            job.kill()
        statuses = [exit_status(h, 5000) for h in handles]
        accounting = job.accounting()
        report.update(statuses=statuses, job_accounting=accounting,
                      cleanup_ms=(time.perf_counter()-start)*1000,
                      ownership=slot.confirm(statuses, accounting))
        if slot.state != "STOPPED" or slot.held:
            emit(report)
            raise AssertionError(f"Exit not fully confirmed: {report}")
        messages.thread.join(5)
        assert not messages.thread.is_alive()
        report["reader_stopped"] = True
        emit(report)
    finally:
        job.close()
        for handle in handles:
            assert exit_status(handle, 5000)["exited"]
            check(close(handle))
        if stdin:
            stdin.close()
        if messages:
            messages.thread.join(5)
        if stdout:
            stdout.close()


def run_case(mode, cache, output):
    output.mkdir()
    evidence = {"case": mode}
    observed = []
    process = None
    with (output / "supervisor.stderr.log").open("w", encoding="utf-8") as error:
        try:
            # Use base interpreter to avoid an additional supervisor launcher indirection.
            process = subprocess.Popen([sys._base_executable, "-B", str(Path(__file__).resolve()),
                "--supervisor", "--case", mode, "--cache", str(cache), "--output", str(output)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=error,
                text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW)
            messages = Messages(process.stdout)
            ready = messages.get(50)
            assert ready["supervisor_pid"] == process.pid
            evidence["ready"] = ready
            # OS process-tree identity evidence (only our numeric Job PIDs).
            ids = ",".join(str(int(pid)) for pid in ready["job_pids"])
            command = (f"Get-CimInstance Win32_Process | Where-Object {{ $_.ProcessId -in @({ids}) }} | "
                       "Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress")
            snapshot = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command],
                capture_output=True, text=True, timeout=12, check=False,
                creationflags=subprocess.CREATE_NO_WINDOW)
            evidence["process_snapshot"] = (json.loads(snapshot.stdout) if snapshot.returncode == 0 else
                {"unavailable": True, "returncode": snapshot.returncode, "error": snapshot.stderr})
            reference = json.loads((HERE / "evidence/persistent-poc-30-1000/result.json").read_text(
                encoding="utf-8"))["reference_vector"]
            evidence["max_abs_vector_difference"] = max(abs(a-b) for a, b in zip(
                reference, ready["normal_result"]["vector"], strict=True))
            assert evidence["max_abs_vector_difference"] <= 1e-6
            evidence["observed_pids"] = ready["job_pids"]
            for pid in ready["job_pids"]:
                observed.append(process_handle(pid))
            process.stdin.write('{"command":"proceed"}\n')
            process.stdin.flush()
            if mode != "normal":
                evidence["checkpoint"] = messages.get(5)
                assert evidence["checkpoint"]["type"] == "held"
            if mode == "parent_kill":
                start = time.perf_counter()
                process.kill()
                process.wait(5)
                evidence["observer_exit_checks"] = [exit_status(h, 5000) for h in observed]
                evidence["cleanup_ms"] = (time.perf_counter()-start)*1000
                assert all(s["exited"] for s in evidence["observer_exit_checks"])
                evidence["observer_survived"] = True
            else:
                evidence["completion"] = messages.get(15)
                assert evidence["completion"]["type"] == "complete"
                process.wait(5)
                assert process.returncode == 0
                evidence["observer_exit_checks"] = [exit_status(h, 5000) for h in observed]
                assert all(s["exited"] for s in evidence["observer_exit_checks"])
            messages.thread.join(5)
            assert not messages.thread.is_alive()
            evidence.update(supervisor_exit_code=process.returncode, observer_reader_stopped=True, passed=True)
        finally:
            if process:
                if process.poll() is None:
                    process.kill()
                    process.wait(5)
                for handle in observed:
                    # Emergency cleanup is recorded; never hides a failed ownership test.
                    if not exit_status(handle, 5000)["exited"]:
                        evidence["emergency_cleanup"] = True
                        check(terminate(handle, 99))
                        assert exit_status(handle, 5000)["exited"]
                    check(close(handle))
                process.stdin.close()
                process.stdout.close()
            (output / "result.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--supervisor", action="store_true")
    parser.add_argument("--case", choices=["normal", "parent_kill", "hang", "termination_timeout"])
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.supervisor:
        supervisor(args)
        return
    assert os.name == "nt" and args.repeats >= 1
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    evidence = {"head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "git_status": subprocess.check_output(["git", "status", "--short"], text=True),
                "python": platform.python_version(), "platform": platform.platform(),
                "production_source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
                "cases": [], "passed": False}
    try:
        with tempfile.TemporaryDirectory(prefix="lifecycle-", dir=ROOT / ".local") as folder:
            source_cache = ROOT / "data/mvp-test/models/models--Qdrant--paraphrase-multilingual-MiniLM-L12-v2-onnx-Q"
            cache = Path(folder) / "models"
            shutil.copytree(source_cache, cache / source_cache.name)
            for repeat in range(args.repeats):
                for mode in ("normal", "parent_kill", "hang", "termination_timeout"):
                    result = run_case(mode, cache, args.output / f"{repeat}-{mode}")
                    evidence["cases"].append(result)
                    emit({"repeat": repeat, "case": mode, "passed": result["passed"]})
        evidence["temporary_cache_removed"] = not Path(folder).exists()
        evidence["passed"] = all(case["passed"] for case in evidence["cases"])
    except BaseException:
        evidence["error"] = traceback.format_exc()
        raise
    finally:
        (args.output / "result.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
