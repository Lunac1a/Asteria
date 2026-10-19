"""One owned model process, one task, bounded IPC; no restart/retry or task queue."""
import json
import logging
import math
import os
from pathlib import Path
import queue
import sys
import threading
import time
import uuid

from fastapi import HTTPException

logger = logging.getLogger(__name__)


class PersistentQueryWorker:
    def __init__(self, model, cache, gate, startup_timeout=30, shutdown_timeout=5,
                 *, defer_failure_cleanup=False):
        self.model, self.cache, self.gate = model, Path(cache), gate
        self.startup_timeout, self.shutdown_timeout = startup_timeout, shutdown_timeout
        self.state = "NEW"
        self.job = None
        self.launcher = self.actual = None
        self.launcher_pid = self.pid = None
        self.stdin = self.stdout = None
        self.io_thread = None
        self.operation = threading.Lock()
        self.gate_held = False
        self.defer_failure_cleanup = defer_failure_cleanup
        self.member_handles = {}

    def _capture_members(self):
        for pid in self.job.pids():
            if pid in self.member_handles or pid in (self.launcher_pid, self.pid):
                continue
            try:
                handle = self.native.process_handle(pid)
            except OSError as exc:
                if getattr(exc, "winerror", None) == 87:  # PID already disappeared.
                    continue
                raise
            try:
                belongs = self.job.contains(handle)
            except Exception:
                self.native.check(self.native.close(handle))
                raise
            if belongs:
                self.member_handles[pid] = handle
            else:  # Process exited and PID was reused outside this Job.
                self.native.check(self.native.close(handle))

    def is_alive(self):
        """Nonblocking probe; caller must exclude lifecycle/embedding operations."""
        if self.state != "READY" or not self.actual:
            return False
        result = self.native.wait(self.actual, 0)
        if result not in (0, 258):
            raise RuntimeError("Unable to inspect model process")
        return result == 258

    def _exchange(self, payload, timeout):
        # Even a blocked pipe write/read cannot make the caller wait indefinitely.
        # This mailbox contains one response, never queued user tasks.
        messages = queue.Queue(maxsize=1)

        def communicate():
            try:
                if payload is not None:
                    self.stdin.write(json.dumps(payload, ensure_ascii=True) + "\n")
                    self.stdin.flush()
                line = self.stdout.readline(1024 * 1024 + 1)
                if not line.endswith("\n") or len(line) > 1024 * 1024:
                    raise RuntimeError("Worker closed or invalid response frame")
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("Invalid worker response")
                messages.put(value)
            except Exception as exc:
                messages.put(exc)

        self.io_thread = threading.Thread(target=communicate, daemon=True, name="query-worker-ipc")
        self.io_thread.start()
        try:
            result = messages.get(timeout=max(0.001, timeout))
        except queue.Empty:
            raise TimeoutError("Query worker deadline exceeded") from None
        self.io_thread.join(timeout=max(0.001, timeout))
        if isinstance(result, Exception):
            raise result
        return result

    def start(self):
        if self.state != "NEW":
            raise RuntimeError("Query worker cannot be restarted")
        if os.name != "nt":
            raise RuntimeError("Persistent query embedding requires Windows 10+ Job Objects")
        from app.services.knowledge import windows_job as native

        self.native = native
        self.state = "STARTING"
        try:
            self.job = native.Job()
            env = {key: value for key, value in os.environ.items() if key.upper() in {
                "SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA"}}
            env.update(OMP_NUM_THREADS="2", TOKENIZERS_PARALLELISM="false", HF_HUB_OFFLINE="1")
            command = [sys.executable, "-B", str(Path(__file__).with_name("query_worker.py")),
                       "--model", self.model, "--cache", str(self.cache)]
            self.launcher, self.launcher_pid, self.stdin, self.stdout = self.job.launch(command, env)
            ready = self._exchange(None, self.startup_timeout)
            if (ready.get("type") != "ready" or ready.get("model") != self.model
                    or type(ready.get("pid")) is not int or ready["pid"] <= 0):
                raise RuntimeError("Invalid query worker READY")
            self.pid = ready["pid"]
            self.actual = native.process_handle(self.pid)
            if not self.job.contains(self.actual):
                raise RuntimeError("Model process is outside owned Job")
            self._capture_members()
            self.state = "READY"
        except Exception:
            self.state = "FAILED"
            self._cleanup()
            raise

    def _cleanup(self):
        """Keep ownership and the shared slot on unconfirmed exit. Never respawn."""
        try:
            if self.job:
                self._capture_members()
                self.job.kill()
                deadline = time.monotonic() + self.shutdown_timeout
                for handle in (self.launcher, self.actual, *self.member_handles.values()):
                    if handle and not self.native.exit_status(handle, max(0, int(
                            (deadline-time.monotonic()) * 1000)))["exited"]:
                        raise RuntimeError("Worker exit could not be confirmed")
                while self.job.accounting()["active_processes"]:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError("Worker descendants did not exit")
                    threading.Event().wait(min(.01, remaining))
                if self.io_thread:
                    self.io_thread.join(max(0, deadline-time.monotonic()))
                    if self.io_thread.is_alive():
                        raise RuntimeError("Worker IPC did not terminate")
                for stream in (self.stdin, self.stdout):
                    if stream:
                        try:
                            stream.close()
                        except OSError:
                            # Windows CRT can report EINVAL rather than EPIPE when
                            # flushing a dead child. Ignore only if close completed;
                            # the Job and IPC thread are already confirmed dead.
                            if not stream.closed:
                                raise
                for handle in (self.launcher, self.actual, *self.member_handles.values()):
                    if handle:
                        self.native.check(self.native.close(handle))
                self.launcher = self.actual = None
                self.member_handles.clear()
                self.job.close()
                self.job = None
            if self.gate_held:
                self.gate.release()
                self.gate_held = False
        except Exception:
            self.state = "DEGRADED"
            raise

    def embed(self, question, timeout):
        if not self.operation.acquire(blocking=False):
            raise HTTPException(429, "Local indexer busy. Please try again shortly.")
        try:
            if self.state != "READY":
                raise HTTPException(503, "Query embedding worker is unavailable")
            if not self.gate.acquire(blocking=False):
                raise HTTPException(429, "Local indexer busy. Please try again shortly.")
            self.gate_held = True
            try:
                if not isinstance(question, str) or not 0 < len(question) <= 16000 or timeout <= 0:
                    raise ValueError("Invalid query or expired deadline")
                task_id = uuid.uuid4().hex
                result = self._exchange({"op": "embed", "task_id": task_id, "texts": [question]}, timeout)
                vectors = result.get("vectors")
                if (result.get("type") != "result" or result.get("task_id") != task_id
                        or not isinstance(vectors, list) or len(vectors) != 1
                        or not isinstance(vectors[0], list) or not 0 < len(vectors[0]) <= 16384
                        or any(type(v) not in (float, int) or not math.isfinite(v) for v in vectors[0])):
                    raise ValueError("Invalid query embedding response")
            except Exception as exc:
                self.state = "FAILED"
                if not self.defer_failure_cleanup:
                    try:
                        self._cleanup()
                    except Exception:
                        # Retain the slot and Job on uncertainty. No automatic retry.
                        logger.exception("Query worker cleanup unconfirmed; state=DEGRADED, slot retained")
                raise HTTPException(503, "Query embedding worker failed or timed out") from exc
            self.gate.release()
            self.gate_held = False
            return vectors[0]
        finally:
            self.operation.release()

    def close(self):
        if not self.operation.acquire(timeout=self.shutdown_timeout):
            self.state = "DEGRADED"
            raise RuntimeError("Query worker still owns an active operation")
        try:
            if self.state == "READY":
                self.state = "STOPPING"
                try:
                    task_id = uuid.uuid4().hex
                    result = self._exchange({"op": "stop", "task_id": task_id}, self.shutdown_timeout)
                    if result != {"type": "stopped", "task_id": task_id}:
                        raise RuntimeError("Invalid stop acknowledgement")
                except Exception:
                    pass  # Bounded cooperative shutdown, then tree-wide termination.
            self._cleanup()
            self.state = "STOPPED"
        finally:
            self.operation.release()
