"""Single-process, best-effort telemetry. No disk I/O on submission paths."""
import atexit
from collections import deque
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import threading


class _Handler(RotatingFileHandler):
    def handleError(self, record):
        # Logging's default handler prints record contents and swallows I/O errors.
        raise OSError("Telemetry write failed") from None


def _write(path, line):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    handler = _Handler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    try:
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.emit(logging.LogRecord("runtime_metrics", logging.INFO, "", 0, line, (), None))
    finally:
        handler.close()


class TelemetryWriter:
    def __init__(self, http_capacity=1024, lifecycle_capacity=256, sink=None):
        self.capacity = {"http": http_capacity, "lifecycle": lifecycle_capacity}
        self.queues = {key: deque() for key in self.capacity}
        self.condition = threading.Condition()
        self.thread = None
        self.accepting = True
        self.active = False
        self.burst = 0
        self.sink = sink
        self.counts = {key: dict(accepted=0, dropped=0, written=0, failed=0) for key in self.capacity}

    def submit(self, event, path):
        lane = "lifecycle" if event.get("kind") == "worker_lifecycle" else "http"
        try:
            # Freeze the event and destination before request ContextVars/settings change.
            line = json.dumps(event, separators=(",", ":"), allow_nan=False)
            with self.condition:
                if not self.accepting or len(self.queues[lane]) >= self.capacity[lane]:
                    self.counts[lane]["dropped"] += 1
                    return False
                if self.thread is None:
                    thread = threading.Thread(target=self._run, daemon=True, name="telemetry-writer")
                    thread.start()
                    self.thread = thread
                self.queues[lane].append((path, line))
                self.counts[lane]["accepted"] += 1
                self.condition.notify_all()
            return True
        except Exception:
            with self.condition:
                self.counts[lane]["dropped"] += 1
            return False

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: any(self.queues.values()) or not self.accepting)
                if not any(self.queues.values()):
                    return
                lane = ("lifecycle" if self.queues["lifecycle"] and
                        (self.burst < 4 or not self.queues["http"]) else "http")
                self.burst = min(4, self.burst + 1) if lane == "lifecycle" else 0
                path, line = self.queues[lane].popleft()
                self.active = True
            outcome = "written"
            try:
                (self.sink or _write)(path, line)
            except BaseException:
                # One failed item is not retried; the writer survives for subsequent items.
                # Never log unrestricted exception text or the rejected record.
                outcome = "failed"
            finally:
                with self.condition:
                    self.counts[lane][outcome] += 1
                    self.active = False
                    self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            return {key: {**self.counts[key], "queued": len(self.queues[key]),
                          "capacity": self.capacity[key]} for key in self.capacity}

    def flush(self, timeout=5):
        """Bounded diagnostic/test drain; never called by HTTP or recovery paths."""
        with self.condition:
            return self.condition.wait_for(lambda: not self.active and not any(self.queues.values()), timeout)

    def close(self, timeout=0):
        """Reject new items; let the daemon drain. Never wait indefinitely at exit."""
        with self.condition:
            self.accepting = False
            self.condition.notify_all()
            thread = self.thread
        if thread:
            thread.join(max(0, timeout))
            return not thread.is_alive()
        return True


writer = TelemetryWriter()
atexit.register(writer.close)


def submit(event):
    from app.core.config import settings
    if settings.RUNTIME_METRICS_ENABLED:
        # abspath is lexical; do not resolve/stat/create the path on the caller.
        path = os.path.abspath(os.path.join(settings.LOCAL_DATA_DIR, "runtime", "metrics.jsonl"))
        return writer.submit(event, path)
    return False
