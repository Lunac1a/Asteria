"""Single lifecycle owner for query embedding; no queued or retried requests."""
import uuid
from datetime import datetime, timezone
import threading
import time
from collections import deque

from fastapi import HTTPException
from app.services.knowledge.persistent_query import PersistentQueryWorker

from app.services.knowledge import lifecycle_events


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
        self.supervisor_run_id = str(uuid.uuid4())
        self.worker_instance_id = None
        self.event_sequence = 0

    def _event(self, event_type, reason="unknown", **fields):
        # Called under condition; immutable scalar snapshot, no writer I/O here.
        try:
            self.event_sequence += 1
            lifecycle_events.emit(dict(
                kind="worker_lifecycle", version="worker-v1",
                timestamp_utc=datetime.now(timezone.utc).isoformat(),
                sequence=self.event_sequence, event_type=event_type, reason_code=reason,
                supervisor_run_id=self.supervisor_run_id,
                worker_instance_id=self.worker_instance_id,
                attempt=self.restarts,
                budget=dict(limit=self.restart_limit, window_seconds=self.window_seconds,
                            window_restarts=sum(t > self.clock() - self.window_seconds for t in self.restart_times),
                            cycle_attempts=self.cycle_attempts,
                            failed_cycles=self.failed_cycles, failed_cycle_limit=self.failed_cycle_limit),
                **fields))
        except Exception:
            pass

    def _state(self, state, event_type=None, reason="unknown", **fields):
        # All calls hold condition. No model/IPC/cleanup work under this lock.
        previous = self.state
        self.state = state
        if event_type or state == "DEGRADED":
            self._event(event_type or "supervisor_degraded", reason,
                        previous_state=previous, new_state=state, **fields)
            if state == "DEGRADED" and event_type:
                self._event("supervisor_degraded", reason, previous_state=previous, new_state=state)
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
                self._state("DEGRADED", reason="startup_deadline")
                raise TimeoutError("Query supervisor startup deadline exceeded")
            if self.state != "READY":
                raise RuntimeError("Query worker initialization failed; startup aborted")

    def _initialize(self):
        with self.condition:
            previous_instance = self.worker_instance_id
            self.worker_instance_id = str(uuid.uuid4())
            self._event("initialization_started", "startup", replaces_worker_instance_id=previous_instance)
            if self.restarts:
                self._event("restart_attempted", "budget_available")
        try:
            worker = self.factory()
            with self.condition:
                self.worker = worker  # Retain ownership even when start fails.
            worker.start()  # Slow initialization OUTSIDE the supervisor lock.
            with self.condition:
                if self.stop.is_set():
                    return
                self.ready_since = self.clock()
                self._state("READY", "initialization_completed", "ready", pid=worker.pid)
                if self.restarts:
                    self._event("restart_completed", "ready")
        except Exception:
            with self.condition:
                self._event("initialization_failed")
                if self.restarts:
                    self._event("restart_failed")
                self._state("DEGRADED" if self.worker and self.worker.state == "DEGRADED" else "FAILED")

    def _terminate(self):
        with self.condition:
            worker = self.worker
            self._state("TERMINATING", "cleanup_started" if worker else None, "cleanup")
        try:
            if worker:
                worker.close()  # Wait/Job accounting outside the supervisor lock.
                if worker.job is not None or worker.gate_held:
                    raise RuntimeError("Worker still owns resources after close")
            if worker:
                with self.condition:
                    self._event("cleanup_confirmed", "checks_passed", cleanup_result="confirmed")
            return True
        except Exception:
            with self.condition:
                self._event("cleanup_unconfirmed", "cleanup_uncertain", cleanup_result="unconfirmed")
                self._state("DEGRADED", reason="cleanup_uncertain")
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
                        failure_reason = "process_not_alive"
                        try:
                            alive = self.worker.is_alive()  # Zero-time OS probe only.
                        except Exception:
                            alive = False
                            failure_reason = "liveness_probe_failed"
                        if alive:
                            # READY alone (or one successful query) cannot reset a
                            # flapping worker's failure history. Require a full window.
                            if self.clock() - self.ready_since >= self.window_seconds:
                                self.failed_cycles = 0
                                self.cycle_attempts = 0
                                self.condition.notify_all()
                            self.condition.wait(timeout=.1)
                            continue
                        self._state("FAILED", "failure_detected", failure_reason)
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
                        self._state("MANUAL_REQUIRED", "manual_required", "restart_disabled")
                        continue
                    # A slow failing initializer may outlive the rolling window.
                    # Bound attempts per unstable cycle too, so expiry cannot hide
                    # continuous failures and bypass MANUAL_REQUIRED forever.
                    if (len(self.restart_times) >= self.restart_limit
                            or self.cycle_attempts >= self.restart_limit):
                        self.failed_cycles += 1
                        if self.failed_cycles >= self.failed_cycle_limit:
                            self.exhausted = True
                            self._state("MANUAL_REQUIRED", "manual_required", "failed_cycle_limit")
                        else:
                            self.cooldown_until = (self.restart_times[0] + self.window_seconds
                                                   if self.restart_times else now + self.window_seconds)
                            self._state("COOLDOWN", "cooldown_entered", "restart_budget", retry_at_monotonic=self.cooldown_until)
                        continue
                    delay = min(30, self.backoff_seconds * 2 ** min(self.cycle_attempts, 5))
                    self.retry_at = now + delay
                    self._state("FAILED", "restart_scheduled", "budget_available", retry_at_monotonic=self.retry_at)  # Backoff: fail requests immediately.
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
            with self.condition:
                self.exhausted = True
                self._state("DEGRADED", reason="supervisor_failure")
                self.initial_done = True
                self.condition.notify_all()
        finally:
            # No lifecycle calls while a request can still use the old instance.
            with self.condition:
                drained = self.condition.wait_for(lambda: not self.inflight, 30 + self.shutdown_timeout + 1)
            if not drained:
                with self.condition:
                    self._state("DEGRADED", reason="inflight_not_drained")
                    self._event("cleanup_unconfirmed", "inflight_not_drained", cleanup_result="unconfirmed")
            elif self._terminate():
                with self.condition:
                    self._state("STOPPED", "shutdown_completed", "checks_passed")

    def embed(self, question, timeout):
        with self.condition:
            if self.stop.is_set() or self.state != "READY":
                raise HTTPException(503, "Query embedding worker is recovering or unavailable")
            if self.inflight:
                raise HTTPException(429, "Local indexer busy. Please try again shortly.")
            # One discoverer marks failure atomically; other requests see 503.
            failure_reason = "process_not_alive"
            try:
                alive = self.worker.is_alive()
            except Exception:
                alive = False
                failure_reason = "liveness_probe_failed"
            if not alive:
                self._state("FAILED", "failure_detected", failure_reason)
                raise HTTPException(503, "Query embedding worker is recovering or unavailable")
            worker = self.worker
            self.inflight = True
        try:
            return worker.embed(question, min(30, timeout))
        finally:
            with self.condition:
                self.inflight = False
                if worker.state in {"FAILED", "DEGRADED"}:
                    self._state(worker.state, "failure_detected", "unknown")
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
                    self._state("DEGRADED", reason="shutdown_deadline")
                raise RuntimeError("Query supervisor shutdown could not be confirmed")
        elif self.worker is None:
            with self.condition:
                self._state("STOPPED", "shutdown_completed", "checks_passed")
        if self.state != "STOPPED":
            raise RuntimeError("Query supervisor is DEGRADED; resources remain owned")
