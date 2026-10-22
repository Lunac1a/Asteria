# Worker lifecycle evidence

When `RUNTIME_METRICS_ENABLED` is true, lifecycle records share
`LOCAL_DATA_DIR/runtime/metrics.jsonl` and its existing rotation policy.
Filter `kind=worker_lifecycle`, `version=worker-v1`; group by `supervisor_run_id`
and order by `sequence`. HTTP records are a different kind.

Each initialization attempt receives a new `worker_instance_id`, before factory
creation. `initialization_started.replaces_worker_instance_id` links generations,
including failed initialization attempts. PID is supplementary, recorded at READY.
`attempt` is the cumulative charged restart count (initial startup is zero).

All records contain UTC timestamp, sequence, event type, reason code and budget
context. Optional fields are instance IDs, state transition endpoints, PID,
`retry_at_monotonic` and `cleanup_result`. Budget contains restart limit/window,
current window count, cycle attempts, failed cycles and failed-cycle limit.
Monotonic deadlines are meaningful only in their originating process.

Instrumentation boundaries:

- Initialization: before factory creation; completed only when Supervisor publishes
  READY; failure after initialization raises. Restart attempted/completed/failed
  accompany initialization only for charged restart attempts.
- Failure detected: failed liveness check or return from a failed embedding call.
  Probe exceptions are distinct from a negative probe. Embedding failure details
  remain `unknown`; no exception message is serialized.
- Cleanup: before close, confirmed only after close returns and the Job/slot
  ownership check passes; otherwise unconfirmed and DEGRADED. No cleanup event is
  emitted when no Worker exists.
- Scheduling, cooldown and manual intervention: existing policy decision branches,
  with counters as observed at that decision. Zero restart limit is
  `restart_disabled`; exhausted failure cycles are `failed_cycle_limit`.
- Shutdown: completed when STOPPED is published. Failed draining or cleanup is
  explicitly unconfirmed/degraded. No startup/readiness policy changes.

Delivery has independent bounded queues: 256 lifecycle and 1024 HTTP/client
records, plus at most one in-flight write. Both submission paths freeze the
payload/destination and never wait for disk writes or queue capacity. One lazy
daemon writer serializes rotation and writes, preserving FIFO within each queue.
It prioritizes up to four lifecycle records then one waiting HTTP record; global
chronological order across kinds is not guaranteed. Full queues drop the new
record. `telemetry_writer.writer.snapshot()` exposes per-queue accepted/dropped/
written/failed counters, depth and capacity, without disk I/O. Serialization and
thread-start failures count as drops; write failures count as failed and are not
retried. Even a sink exception cannot terminate the delivery loop; subsequent
records continue. Counters are process-local and not persisted.
Queue/write errors are best effort: recovery continues; sequence gaps can reveal
missing interior events.
No fsync, restart replay, cross-process order or complete delivery is guaranteed.
Process termination (including normal interpreter exit) can lose queued tail
events; rotation can remove history. Tests use bounded `writer.flush(timeout)`
before collecting files. This method must not be called on HTTP/recovery paths.
At interpreter exit, `close(0)` rejects new records without waiting for the daemon;
there is no guaranteed shutdown flush. A hung disk can leave that one writer
blocked; callers keep running and queues eventually drop new records. No
replacement threads are spawned to bypass it. A bounded explicit close can wait
for draining in tests or offline tools, but no application shutdown policy changed.

Only allowlisted fields/categories are written. Raw exception text, stderr,
prompts and credentials are excluded. Exit codes are intentionally absent: the
existing Supervisor liveness probe does not supply one, and post-termination
cleanup codes must not be represented as the original failure's code. A negative
liveness result establishes unavailability, not who terminated the process.
Worker-to-HTTP correlation and detailed cleanup-check evidence remain outside scope.
