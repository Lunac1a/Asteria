"""Isolated paired latency, 1000-query memory and in-flight crash PoC."""
import argparse
import ctypes
from ctypes import wintypes
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
from unittest.mock import patch
import uuid

import workspace_creation_concurrency as f
from profile_embedding_worker import QUERY, distribution
from app.core.config import settings
from app.services.knowledge import retrieval


class Worker:
    def __init__(self, source, model, cache, stderr_path):
        env = {k: v for k, v in os.environ.items() if k.upper() in {
            "SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA"}}
        env.update(OMP_NUM_THREADS="2", TOKENIZERS_PARALLELISM="false", HF_HUB_OFFLINE="1")
        self.stderr = stderr_path.open("w", encoding="utf-8")
        self.started = time.perf_counter()
        self.process = subprocess.Popen([sys.executable, "-B", str(Path(__file__).with_name("persistent_embedding_child.py")),
            "--source", str(source), "--model", model, "--cache", str(cache)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr,
            text=True, encoding="utf-8", env=env,
            creationflags=subprocess.CREATE_NO_WINDOW)
        self.messages = queue.Queue()

        def reader():
            try:
                for line in self.process.stdout:
                    self.messages.put(json.loads(line))
            except BaseException as exc:
                self.messages.put({"type": "reader_error", "error": str(exc)})
            finally:
                self.messages.put(None)

        self.reader = threading.Thread(target=reader, daemon=True)
        self.actual_handle = None
        self.actual_pid = None
        self.reader.start()

    def receive(self, timeout=10):
        try:
            value = self.messages.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError("Worker response deadline exceeded") from None
        if value is None:
            raise RuntimeError("Worker EOF before completing task")
        if value.get("type") == "reader_error":
            raise RuntimeError(value["error"])
        if value.get("type") == "ready":
            self.actual_pid = value["pid"]
            open_process = ctypes.windll.kernel32.OpenProcess
            open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            open_process.restype = wintypes.HANDLE
            self.actual_handle = open_process(0x100000 | 0x0001, False, self.actual_pid)
            if not self.actual_handle:
                raise ctypes.WinError()
        return value

    def terminate_actual(self):
        terminate = ctypes.windll.kernel32.TerminateProcess
        terminate.argtypes = [wintypes.HANDLE, wintypes.UINT]
        if not terminate(self.actual_handle, 1):
            raise ctypes.WinError()

    def send(self, **payload):
        task_id = str(uuid.uuid4())
        self.process.stdin.write(json.dumps({"task_id": task_id, **payload}) + "\n")
        self.process.stdin.flush()
        return task_id

    def embed(self, sample=False):
        start = time.perf_counter()
        task_id = self.send(texts=[QUERY], sample_memory=sample)
        result = self.receive()
        assert result["task_id"] == task_id and result["type"] == "result"
        result["roundtrip_ms"] = (time.perf_counter()-start)*1000
        return result

    def close(self):
        forced = False
        if self.process.poll() is None:
            try:
                task_id = self.send(op="stop")
                assert self.receive(5) == {"type": "stopped", "task_id": task_id}
                self.process.wait(5)
            except Exception:
                forced = True
                if self.actual_handle:
                    self.terminate_actual()
                else:
                    self.process.kill()
                self.process.wait(5)
        self.process.stdin.close()
        self.reader.join(5)
        self.process.stdout.close()
        self.stderr.close()
        actual_exited = None
        if self.actual_handle:
            wait = ctypes.windll.kernel32.WaitForSingleObject
            wait.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            actual_exited = wait(self.actual_handle, 5000) == 0
            close = ctypes.windll.kernel32.CloseHandle
            close.argtypes = [wintypes.HANDLE]
            close(self.actual_handle)
            self.actual_handle = None
            assert actual_exited, "Actual worker still alive"
        return {"pid": self.process.pid, "returncode": self.process.returncode,
                "actual_worker_pid": self.actual_pid, "actual_worker_exited": actual_exited,
                "forced_cleanup": forced, "reader_stopped": not self.reader.is_alive(),
                "reaped": self.process.poll() is not None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    source = Path(retrieval.__file__).with_name("index_worker.py")
    evidence = {"head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "python": platform.python_version(), "platform": platform.platform(), "query": QUERY,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "latency_samples": [], "memory_samples": [], "cleanup": [], "passed": False}
    workers = []
    reference = None
    differences = []
    task_ids = set()

    def validate(vector):
        nonlocal reference
        assert len(vector) == 384 and all(math.isfinite(v) for v in vector)
        if reference is None:
            reference = vector
            evidence["reference_vector"] = vector
        difference = max(abs(a-b) for a, b in zip(reference, vector))
        differences.append(difference)
        assert difference <= 1e-6

    def check_result(result):
        assert result["task_id"] not in task_ids
        task_ids.add(result["task_id"])
        assert len(result["vectors"]) == 1
        validate(result["vectors"][0])

    try:
        with tempfile.TemporaryDirectory(dir=f.BACKEND.parent / ".local", prefix="persistent-poc-") as folder:
            assert Path(folder).resolve().is_relative_to((f.BACKEND.parent / ".local").resolve())
            original_cache = f.BACKEND.parent / "data/mvp-test/models/models--Qdrant--paraphrase-multilingual-MiniLM-L12-v2-onnx-Q"
            shutil.copytree(original_cache, Path(folder) / "models" / original_cache.name)
            evidence["model_hashes"] = {str(p.relative_to(original_cache)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in original_cache.rglob("*") if p.is_file() and p.suffix in (".onnx", ".json")}
            with patch.object(settings, "LOCAL_DATA_DIR", folder), patch.object(settings, "EMBEDDING_BACKEND", "fastembed"):
                start = time.perf_counter()
                validate(retrieval.run_worker({"texts": [QUERY]}, 30)["vectors"][0])
                evidence["disposable_first_call_ms"] = (time.perf_counter()-start)*1000
                worker = Worker(source, settings.EMBEDDING_MODEL, Path(folder) / "models", args.output / "worker.stderr.log")
                workers.append(worker)
                ready = worker.receive(30)
                assert ready["type"] == "ready"
                evidence["persistent_startup"] = {**ready, "parent_launch_to_ready_ms": (time.perf_counter()-worker.started)*1000}
                first = worker.embed(True)
                check_result(first)
                evidence["persistent_first_query"] = {k:v for k,v in first.items() if k != "vectors"}
                for pair in range(33):
                    for mode in (["disposable", "persistent"] if pair % 2 == 0 else ["persistent", "disposable"]):
                        start = time.perf_counter()
                        if mode == "disposable":
                            vector = retrieval.run_worker({"texts": [QUERY]}, 30)["vectors"][0]
                            elapsed = (time.perf_counter()-start)*1000
                            validate(vector)
                        else:
                            result = worker.embed()
                            elapsed = result["roundtrip_ms"]
                            check_result(result)
                        evidence["latency_samples"].append({"pair": pair, "mode": mode, "warmup": pair < 3,
                            "ms": elapsed, **({"task_id": result["task_id"]} if mode == "persistent" else {})})
                    if pair % 5 == 2:
                        print(f"Latency pairs {pair+1}/33", flush=True)
                baseline = worker.embed(True)
                check_result(baseline)
                evidence["memory_samples"].append({"query": 0, **baseline["memory"]})
                evidence["memory_query_latencies_ms"] = []
                for index in range(1, 1001):
                    result = worker.embed(index % 50 == 0)
                    check_result(result)
                    evidence["memory_query_latencies_ms"].append(result["roundtrip_ms"])
                    if "memory" in result:
                        evidence["memory_samples"].append({"query": index, **result["memory"]})
                    if index % 250 == 0:
                        print(f"Memory queries {index}/1000", flush=True)
                evidence["cleanup"].append(worker.close())
                workers.remove(worker)
                # Separate worker: kill a task after an explicit accepted checkpoint.
                failure = Worker(source, settings.EMBEDDING_MODEL, Path(folder) / "models", args.output / "failure.stderr.log")
                workers.append(failure)
                failure_ready = failure.receive(30)
                assert failure_ready["type"] == "ready"
                evidence["failure_ready"] = failure_ready
                task_id = failure.send(op="hold")
                assert failure.receive(5) == {"type": "accepted", "task_id": task_id}
                start = time.perf_counter()
                failure.terminate_actual()
                try:
                    failure.receive(5)
                    raise AssertionError("Terminated worker unexpectedly completed a task")
                except RuntimeError as exc:
                    evidence["failure"] = {"task_id": task_id, "error": str(exc),
                        "detect_ms": (time.perf_counter()-start)*1000, "parent_alive": True}
                failure.process.wait(5)
                evidence["cleanup"].append(failure.close())
                workers.remove(failure)
            evidence["latency"] = {mode: distribution([s["ms"] for s in evidence["latency_samples"]
                if s["mode"] == mode and not s["warmup"]]) for mode in ("disposable", "persistent")}
            evidence["correctness"] = {"validated_vectors": len(differences), "max_abs_difference": max(differences),
                "unique_persistent_task_ids": len(task_ids)}
            assert "fastembed" not in sys.modules, "Model library must not be loaded in caller"
            evidence["parent_fastembed_loaded"] = False
            evidence["passed"] = True
        evidence["temporary_files_removed"] = not Path(folder).exists()
    except BaseException:
        evidence["error"] = traceback.format_exc()
        raise
    finally:
        for worker in workers:
            evidence["cleanup"].append(worker.close())
        (args.output / "result.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps({"passed": evidence["passed"], "latency": evidence.get("latency")}))


if __name__ == "__main__":
    main()
