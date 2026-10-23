# Windows single-process incident response

Scope: one Windows API process, existing Job Object Worker Supervisor, PostgreSQL
and one canonical storage root. No automatic operator restart loop or new monitor.
Use alongside `windows-deployment.md`, `worker-lifecycle-events.md`,
`backup-restore.md` and `release-rollback.md`.

## 1. Detect and bound the incident

Record UTC time, approved code SHA (or source snapshot hash), configuration flags,
symptoms and affected capability. Stop new traffic if writes or cleanup are uncertain.
Check existing endpoints without credentials in command history:

```powershell
Invoke-RestMethod http://127.0.0.1:18001/api/health
Invoke-RestMethod http://127.0.0.1:18001/api/ready
./Start-Asteria.ps1 -CheckOnly
```

The script also expects the configured frontend/proxy. A failed frontend check is
not proof the API failed. Health proves reachability; readiness checks the recovery
gate/database, not continuous Worker availability. Inspect Worker events and a
controlled functional request before declaring recovery.

## 2. Identify the component and preserve evidence

Use the actual configured absolute LOCAL_DATA_DIR. Preserve metrics.jsonl and
rotated siblings before rotation removes history. Keep evidence access restricted.
Read complete JSON lines; retain a truncated tail as incomplete evidence, not proof
of corruption or absence of failure. Example sanitized lifecycle view:

```powershell
$incidentRoot = 'D:/ACTUAL/LOCAL_DATA_DIR'
Get-Content -LiteralPath "$incidentRoot/runtime/metrics.jsonl" |
  ForEach-Object { try { $_ | ConvertFrom-Json -ErrorAction Stop } catch {} } |
  Where-Object { $_.kind -eq 'worker_lifecycle' } |
  Sort-Object supervisor_run_id,sequence |
  Select-Object timestamp_utc,supervisor_run_id,sequence,event_type,reason_code,
    worker_instance_id,replaces_worker_instance_id,previous_state,new_state,attempt,budget,cleanup_result
```

For HTTP, retain only trace_id/http_request_id/request_id/flight_id, status,
execution_source/singleflight_role, stage intervals, coverage/unexplained time and
sanitized Provider durations/categories. Do not collect Authorization headers,
environment dumps, raw prompts/answers/documents, unrestricted stderr or raw exception
messages. `request_id` is business idempotency; it is not the HTTP identity.

Worker generations are worker_instance_id, scoped by supervisor_run_id; PID alone
is unsafe due to reuse. Order lifecycle records by sequence within a run, not file
order across telemetry kinds. HTTP-to-Worker association is not directly recorded;
temporal overlap is only a hypothesis. Negative process liveness does not identify
the killer, original exit code, OOM or model defect.

Telemetry is best effort with bounded queues and rotation. Missing events are not
proof of safety. Sequence gaps reveal some losses; tail loss may be invisible.
`writer.snapshot()` and Supervisor snapshots are in-process diagnostic APIs, not
remote endpoints; a new Python process cannot inspect the live process's counters.
Do not add an unsafe debug endpoint to obtain them.

## 3. Decide recovery authority

| Evidence | Operator action |
|---|---|
| failure_detected, cleanup_confirmed, restart_scheduled with budget_available | Let the existing Supervisor recover; observe the replacement link and READY/restart_completed. Do not start a second Worker. |
| COOLDOWN | Respect recorded window/deadline; do not reset counters or restart the API to bypass the budget. |
| manual_required / MANUAL_REQUIRED | Record reason and budget, stop affected traffic and escalate. Establish cause and owned-process cleanup before an explicitly approved fresh process start. |
| cleanup_unconfirmed / DEGRADED / shutdown uncertainty | Fail closed: no replacement launch. Inspect owned processes/Job and slot release on the same host; never kill an arbitrary reused PID or remove locks blindly. If ownership cannot be established, remain unavailable and escalate. |
| database/schema/recovery marker failure | Stop writes. Use release rollback or paired backup restore; never delete pending records or .restore-incomplete to make readiness green. |

Restart budgets and Supervisor state are process-local, not durable across API exit.
Preserve the old run's evidence and do not treat a fresh process as a free budget.
Manual intervention is an operator procedure, not a reset API. No manual override
was required or exercised in the accepted single-crash drill.

## 4. Verify service and integrity

Require cleanup confirmation for G1 before G2 initialization and a distinct G2
instance linked to G1. Confirm allowed attempt/window count and stable READY.
Then check health/readiness, authentication, document availability and a controlled
Mock Chat with a fresh request_id. Verify expected evidence/citations, persisted
turn/message consistency, and no duplicate result. Compare relevant pre-incident
table/file invariants when available; normal new Chat writes are expected.

Do not automatically replay an operation with an uncertain outcome; use existing
idempotency semantics and inspect its durable outcome. A read-only successful health
request does not substitute for this check. After a planned shutdown, confirm
lifespan completion and owned process exit; an event alone is not OS exit proof.

## 5. Close or escalate

Record incident time window, impact, event sequence, generations, attempt budget,
automatic actions versus operator actions, verification results, data recovery point,
and cleanup evidence. Separate observed facts, hypotheses and verified root cause.
Record missing evidence explicitly. Restrict raw artifacts; publish sanitized IDs
and timings only. Keep unresolved risks in the handoff and reopen traffic only after
the required checks succeed.

Accepted drill: `.local/incident-20261023/` — idle Worker death, automatic recovery,
real Windows Job/FastEmbed + TCP API + PostgreSQL + local Mock LLM. It does not prove
in-flight request attribution, repeated-crash diagnosis or manual recovery success.
