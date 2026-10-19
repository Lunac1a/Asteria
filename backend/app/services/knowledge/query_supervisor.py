"""Single lifecycle owner for query embedding; no queued or retried requests."""
import logging
import threading
import time
from collections import deque

from fastapi import HTTPException
from app.services.knowledge.persistent_query import PersistentQueryWorker

logger = logging.getLogger(__name__)


class QueryWorkerSupervisor:
    def __init__(self, model, cache, gate, startup_timeout=30, shutdown_timeout=5,
                 restart_limit=3, backoff_seconds=1, window_seconds=600,
                 failed_cycle_limit=3, *, worker_factory=None, clock=time.monotonic):
        self.factory = worker_factory or (lambda: PersistentQueryWorker(
            model, cache, gate, startup_timeout, shutdown_timeout, defer_failure_cleanup=True))
        self.startup_timeout = startup_timeout
        self.shutdown_timeout = shutdown_timeout
        self.restart_limit = restart_limit
        self.backoff_seconds = backoff_seconds
        self.window_seconds = window_seconds
        self.failed_cycle_limit = failed_cycle_limit
        self.clock = clock
        self.restart_times = deque()
        self.failed_cycles = 0
        self.cycle_attempts = 0
        self.ready_since = None
        self.cooldown_until = None
        self.retry_at = None
        self.condition = threading.Condition()
        self.stop = threading.Event()
        self.state = "NEW"
        self.worker = None
        self.thread = None
        self.inflight = False
        self.restarts = 0
        self.exhausted = False
        self.initial_done = False

    def _state(self, state):
        # All calls hold condition. No model/IPC/cleanup work under this lock.
        self.state = state
        self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            return {"state": self.state, "restarts": self.restarts,
                    "exhausted": self.exhausted, "inflight": self.inflight,
                    "window_restarts": sum(t > self.clock() - self.window_seconds for t in self.restart_times),
                    "failed_cycles": self.failed_cycles, "cooldown_until": self.cooldown_until,
                    "retry_at": self.retry_at,
                    "pid": self.worker.pid if self.worker else None}

    def start(self):
        with self.condition:
            if self.state != "NEW":
                raise RuntimeError("Supervisor has already started")
            self._state("STARTING")
            self.thread = threading.Thread(target=self._run, daemon=True, name="query-supervisor")
            self.thread.start()
            if not self.condition.wait_for(lambda: self.initial_done,
                                           self.startup_timeout + self.shutdown_timeout + 5):
                self.stop.set()
                self._state("DEGRADED")
                raise TimeoutError("Query supervisor startup deadline exceeded")
            if self.state != "READY":
                raise RuntimeError("Query worker initialization failed; startup aborted")

    def _initialize(self):
        try:
            worker = self.factory()
            with self.condition:
                self.worker = worker  # Retain ownership even when start fails.
            worker.start()  # Slow initialization OUTSIDE the supervisor lock.
            with self.condition:
                if self.stop.is_set():
                    return
                self.ready_since = self.clock()
                self._state("READY")
        except Exception:
            logger.exception("Query worker initialization failed")
            with self.condition:
                self._state("DEGRADED" if self.worker and self.worker.state == "DEGRADED" else "FAILED")

    def _terminate(self):
        with self.condition:
            self._state("TERMINATING")
            worker = self.worker
        try:
            if worker:
                worker.close()  # Wait/Job accounting outside the supervisor lock.
                if worker.job is not None or worker.gate_held:
                    raise RuntimeError("Worker still owns resources after close")
            return True
        except Exception:
            logger.exception("Query supervisor cleanup unconfirmed; replacement forbidden")
            with self.condition:
                self._state("DEGRADED")
            return False

    def _run(self):
        try:
            self._initialize()
            with self.condition:
                self.initial_done = True
                # Initial failure is fail-closed, not a startup restart loop.
                if self.state != "READY":
                    self.exhausted = True
                self.condition.notify_all()
            while not self.stop.is_set():
                with self.condition:
                    if self.state == "DEGRADED" or self.exhausted or self.inflight:
                        self.condition.wait(timeout=.1)
                        continue
                    if self.state == "COOLDOWN":
                        remaining = self.cooldown_until - self.clock()
                        if remaining > 0:
                            self.condition.wait(timeout=remaining)
                            continue
                        self.cooldown_until = None
                        self.cycle_attempts = 0
                        self._state("FAILED")
                    if self.state == "READY":
                        try:
                            alive = self.worker.is_alive()  # Zero-time OS probe only.
                        except Exception:
                            alive = False
                        if alive:
                            # READY alone (or one successful query) cannot reset a
                            # flapping worker's failure history. Require a full window.
                            if self.clock() - self.ready_since >= self.window_seconds:
                                self.failed_cycles = 0
                                self.cycle_attempts = 0
                                self.condition.notify_all()
                            self.condition.wait(timeout=.1)
                            continue
                        self._state("FAILED")
                # Only this thread performs lifecycle transitions and replacement.
                if not self._terminate():
                    continue
                with self.condition:
                    if self.stop.is_set():
                        break
                    now = self.clock()
                    while self.restart_times and self.restart_times[0] <= now - self.window_seconds:
                        self.restart_times.popleft()
                    if self.restart_limit == 0:
                        self.exhausted = True
                        self._state("MANUAL_REQUIRED")
                        continue
                    # A slow failing initializer may outlive the rolling window.
                    # Bound attempts per unstable cycle too, so expiry cannot hide
                    # continuous failures and bypass MANUAL_REQUIRED forever.
                    if (len(self.restart_times) >= self.restart_limit
                            or self.cycle_attempts >= self.restart_limit):
                        self.failed_cycles += 1
                        if self.failed_cycles >= self.failed_cycle_limit:
                            self.exhausted = True
                            self._state("MANUAL_REQUIRED")
                        else:
                            self.cooldown_until = (self.restart_times[0] + self.window_seconds
                                                   if self.restart_times else now + self.window_seconds)
                            self._state("COOLDOWN")
                        continue
                    delay = min(30, self.backoff_seconds * 2 ** min(self.cycle_attempts, 5))
                    self.retry_at = now + delay
                    self._state("FAILED")  # Backoff: fail requests immediately.
                    # A clock injection plus condition notification makes deadlines
                    # deterministic in tests; production uses monotonic time only.
                    while not self.stop.is_set() and self.clock() < self.retry_at:
                        self.condition.wait(timeout=self.retry_at - self.clock())
                    self.retry_at = None
                    if self.stop.is_set():
                        break
                    now = self.clock()
                    while self.restart_times and self.restart_times[0] <= now - self.window_seconds:
                        self.restart_times.popleft()
                    self.restart_times.append(now)  # Charge actual start, not reservation.
                    self.restarts += 1
                    self.cycle_attempts += 1
                    self._state("STARTING")
                self._initialize()
        except Exception:
            logger.exception("Query supervisor failed; automatic recovery disabled")
            with self.condition:
                self.exhausted = True
                self._state("DEGRADED")
                self.initial_done = True
                self.condition.notify_all()
        finally:
            # No lifecycle calls while a request can still use the old instance.
            with self.condition:
                drained = self.condition.wait_for(lambda: not self.inflight, 30 + self.shutdown_timeout + 1)
            if not drained:
                with self.condition:
                    self._state("DEGRADED")
            elif self._terminate():
                with self.condition:
                    self._state("STOPPED")

    def embed(self, question, timeout):
        with self.condition:
            if self.stop.is_set() or self.state != "READY":
                raise HTTPException(503, "Query embedding worker is recovering or unavailable")
            if self.inflight:
                raise HTTPException(429, "Local indexer busy. Please try again shortly.")
            # One discoverer marks failure atomically; other requests see 503.
            try:
                alive = self.worker.is_alive()
            except Exception:
                alive = False
            if not alive:
                self._state("FAILED")
                raise HTTPException(503, "Query embedding worker is recovering or unavailable")
            worker = self.worker
            self.inflight = True
        try:
            return worker.embed(question, min(30, timeout))
        finally:
            with self.condition:
                self.inflight = False
                if worker.state in {"FAILED", "DEGRADED"}:
                    self._state(worker.state)
                self.condition.notify_all()

    def close(self):
        with self.condition:
            self.stop.set()
            self.condition.notify_all()
            thread = self.thread
        if thread:
            thread.join(self.startup_timeout + 30 + 3 * self.shutdown_timeout + 6)
            if thread.is_alive():
                with self.condition:
                    self._state("DEGRADED")
                raise RuntimeError("Query supervisor shutdown could not be confirmed")
        elif self.worker is None:
            with self.condition:
                self._state("STOPPED")
        if self.state != "STOPPED":
            raise RuntimeError("Query supervisor is DEGRADED; resources remain owned")
