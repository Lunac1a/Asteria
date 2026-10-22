"""Sanitized lifecycle submission; never do disk I/O in lifecycle locks.

FIFO within the independent lifecycle lane. Sequence gaps mean dropped events; termination may
lose queued events. Uses Runtime Metrics enablement, rotation and error handling.
No durability/fsync guarantee and no cross-process ordering guarantee.
"""
from uuid import UUID
from app.services.runtime_metrics import write_event

EVENTS = frozenset({
    "initialization_started", "initialization_completed", "initialization_failed",
    "failure_detected", "cleanup_started", "cleanup_confirmed", "cleanup_unconfirmed",
    "restart_scheduled", "restart_attempted", "restart_completed", "restart_failed",
    "cooldown_entered", "manual_required", "supervisor_degraded", "shutdown_completed",
})
REASONS = frozenset({
    "unknown", "startup", "ready", "cleanup", "checks_passed", "cleanup_uncertain",
    "process_not_alive", "liveness_probe_failed", "restart_disabled", "failed_cycle_limit",
    "restart_budget", "budget_available", "startup_deadline", "supervisor_failure",
    "inflight_not_drained", "shutdown_deadline",
})
STATES = frozenset({"NEW", "STARTING", "READY", "FAILED", "TERMINATING", "DEGRADED",
                    "COOLDOWN", "MANUAL_REQUIRED", "STOPPED"})


def sanitize(item):
    # No arbitrary fields, strings, exception messages, or nested payloads.
    from datetime import datetime
    if item.get("event_type") not in EVENTS:
        raise ValueError("Invalid lifecycle event")
    result = dict(kind="worker_lifecycle", version="worker-v1",
                  event_type=item["event_type"],
                  timestamp_utc=datetime.fromisoformat(item["timestamp_utc"]).isoformat(),
                  reason_code=item["reason_code"] if item.get("reason_code") in REASONS else "unknown")
    for key in ("supervisor_run_id", "worker_instance_id", "replaces_worker_instance_id"):
        if item.get(key) is not None:
            result[key] = str(UUID(item[key]))
    for key in ("previous_state", "new_state"):
        if item.get(key) in STATES:
            result[key] = item[key]
    for key in ("sequence", "attempt", "pid"):
        if type(item.get(key)) is int:
            result[key] = item[key]
    if type(item.get("retry_at_monotonic")) in (int, float):
        result["retry_at_monotonic"] = item["retry_at_monotonic"]
    if item.get("cleanup_result") in {"confirmed", "unconfirmed"}:
        result["cleanup_result"] = item["cleanup_result"]
    result["budget"] = {key: value for key, value in item.get("budget", {}).items()
                        if key in {"limit", "window_seconds", "window_restarts", "cycle_attempts",
                                   "failed_cycles", "failed_cycle_limit"} and type(value) in (int, float)}
    return result

def emit(item):
    try:
        write_event(sanitize(item))
    except Exception:
        pass
