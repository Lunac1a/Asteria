# Phase 4 / Unit 1 — Observability Acceptance

Date: 2026-10-22 (Australia/Sydney). **Final decision: PASS within the verified single-process scope.**

## 1. Scope and Git state

HEAD remains `76ed0978407c06711c46b69deff4b981c147164a`, the Phase 3 acceptance-evidence baseline. All Phase 4 work remains uncommitted; no push, migration, new dependency or recovery-policy change was made.

Unit 1 provides distinct HTTP/business/Flight correlation, inclusive Chat stage intervals and interval-union coverage, and sanitized Worker generation/recovery events. Step 9 fixes the shared telemetry writer's HTTP event-loop blocking.

Step 9 changes:

- New `backend/app/services/telemetry_writer.py`: one background writer with independent bounded lanes and memory-only accounting.
- `backend/app/services/runtime_metrics.py`: `write_event` now submits instead of writing synchronously.
- `backend/app/services/knowledge/lifecycle_events.py`: retains schema filtering; delegates to the shared writer instead of owning a second writer thread.
- New `backend/tests/test_telemetry_writer.py`; updated writer-boundary mocks/assertions in `test_singleflight_metrics.py` and `test_worker_lifecycle_events.py`.
- Updated `backend/docs/worker-lifecycle-events.md` and this report.

The existing Chat route, Singleflight, workspace coordination, Supervisor and Persistent Worker changes are byte-equivalent at the tracked-diff level to the start of Step 9; see [preservation.json](.local/unit1-step9-20261022/preservation.json). All earlier untracked tests/experimental evidence remain present. The previous FAIL report is archived at [previous-FAIL-summary.md](.local/unit1-step9-20261022/previous-FAIL-summary.md); the original blocker evidence was not overwritten.

## 2. Root cause, architecture and guarantees

Previously, a lifecycle writer held the shared file lock while HTTP middleware synchronously waited for that lock on the event loop. This prevented another async endpoint from starting. The fix moves **all telemetry disk writes**, including HTTP/client metrics and lifecycle records, to one daemon writer. Production call paths were reviewed: HTTP middleware, client-timing receiver and lifecycle emitter all use the submitting `write_event` boundary.

| Policy | Implementation |
|---|---|
| Capacity | HTTP/client: 1024 records; lifecycle: 256 records; at most one additional in-flight write. Capacities are independent. |
| Submission | Freeze JSON payload and destination; short in-memory synchronization only. No filesystem resolution, directory creation, file lock or I/O on caller paths. |
| Scheduling | FIFO within each lane; up to four lifecycle records, then one waiting HTTP record. No global chronological ordering across lanes. |
| Saturation | Drop the new record in that lane; never wait for capacity. |
| Accounting | `telemetry_writer.writer.snapshot()` exposes accepted/dropped/written/failed, queue depth and capacity per lane. No disk dependency or raw error text. |
| Failure | Serialization/thread-start failure counts as dropped. Failed writes count as failed, are not retried, and do not terminate the delivery loop. Standard logging error handling is overridden to avoid dumping record/exception contents. |
| Storage | Same `runtime/metrics.jsonl`, 2,000,000-byte rotation threshold and 3 backups. One writer serializes file operations in the supported process. |
| Startup/exit | One lazily started daemon. Interpreter exit calls `close(0)`, rejecting new submissions without waiting. A hung disk does not spawn replacement writers. |
| Flush | Explicit `flush(timeout)` is bounded and intended for tests/offline collection, never HTTP/recovery execution. No durable delivery, fsync, retry or guaranteed exit flush. |

Event schemas, correlation IDs, stage clocks/union coverage, feature defaults and readiness behavior remain unchanged. Writer delays are outside HTTP stage timing; this is now background work rather than an event-loop stall. No HTTP-to-Worker correlation was introduced.

## 3. Original blocker: before and after

Before: [.local/unit1-acceptance-20261022/shared-writer-result.json](.local/unit1-acceptance-20261022/shared-writer-result.json) shows both `first_http_completed_while_writer_held=false` and `second_endpoint_entered_while_writer_held=false`.

After: `test_original_blocker_http_and_recovery_complete_before_disk_release` holds the real sink behind an event barrier and verifies, **before releasing it**:

- Two HTTP requests complete with correct responses; the second async endpoint executes.
- Supervisor startup, one controlled fixture failure/replacement, READY and shutdown complete.
- After release, 16 valid records are written: 2 HTTP and 14 lifecycle, zero dropped/failed.

The `WRITER_BLOCKER_AFTER` result is in [focused.log](.local/unit1-step9-20261022/focused.log) and [backend-regression-final.log](.local/unit1-step9-20261022/backend-regression-final.log). This uses deterministic synchronization, not a latency benchmark or a real crash; the separate Windows experiment below supplies real process evidence.

Other focused checks prove independent queue saturation/accounting, weighted lane scheduling, subsequent delivery after an actual logging-handler failure, bounded close/flush, payload freezing and serialization failure accounting. Four concurrent producers submit 120 mixed-schema records through a reduced test rotation threshold: all 120 IDs occur exactly once across valid JSONL files. This verifies the rotation mechanism in one process, not multi-process rotation safety. Sensitive synthetic exception text does not reach stderr or accounting records.

## 4. Final regression and PostgreSQL results

| Run | Result | Environment / evidence |
|---|---|---|
| Complete backend discovery | **350 discovered: 342 passed, 8 PostgreSQL skips, zero failures/errors**, 170.061 s | Isolated synthetic/SQLite/ASGI fixtures plus Windows subprocess cases; [final log](.local/unit1-step9-20261022/backend-regression-final.log). |
| PostgreSQL recovery/startup modules | **38 discovered: 37 passed, 1 SQLite-only rejection test skipped** | Real disposable PostgreSQL 18.6, exact data-directory validation; [PG log](.local/unit1-step9-20261022/postgres-recovery.log). All eight previously skipped PostgreSQL cases executed and passed here. |
| Phase 3 workspace concurrency fixture | **PASS** | Real TCP HTTP + PostgreSQL; quota boundary, held request rejection and retry/final state; [result](.local/unit1-step9-20261022/workspace-concurrency.json). |
| Phase 3 session Chat fixture, no `--load` | **PASS** | Real TCP HTTP + PostgreSQL + local Mock LLM + offline persistent Worker. Same-session/deletion exclusion, duplicate response sharing, deferred COMMIT failure/rollback and resource release; [result](.local/unit1-step9-20261022/session-chat/result.json). Expected injected COMMIT exception appears in the log; it is not a failed check. |
| Focused writer/runtime/lifecycle run | **31 passed** | Overlaps final discovery; [focused.log](.local/unit1-step9-20261022/focused.log). |
| Ruff / diff check | **PASS** | `ruff check backend/app backend/tests`; `git diff --check`. Existing line-ending warnings are not failures. |

Do not add these counts: discovery and PostgreSQL/focused runs overlap. The first Step 9 discovery had one stale test mock referencing the removed `runtime_metrics.RotatingFileHandler`; it is preserved in `backend-regression.log`. The test now injects failure at the new writer and asserts in-memory failure accounting. The final complete run passed.

Docker Desktop was initially stopped and was started using its existing installation. Existing local `postgres:18-alpine` was used with `--pull=never`: no dependencies/images downloaded. Two new containers had no host data mounts and loopback-only bindings. The recovery/workspace container used port 11030; the existing session fixture required 55489. Both used fresh data directories with the fixture-required prefixes. They were stopped and automatically removed after verification; [cleanup evidence](.local/unit1-step9-20261022/postgres-cleanup.json). Unrelated containers were not modified. PostgreSQL 18.6 is the verified version, not a claim of a PostgreSQL 16 rerun.

## 5. Engineering evidence retained

- Current synthetic timing: injected 300 ms commit changes HTTP 50→350 ms and persistence 0→300 ms; nested generation/Provider remain 50 ms. Current separate wall-clock test: injected wait 303.700 ms, HTTP 12.37→313.48 ms, persistence 5.150→306.409 ms; Provider call count stays one. These are attribution checks, not performance targets.
- Earlier [.local/phase4-latency-20261021/](.local/phase4-latency-20261021/) contains real TCP/PostgreSQL/Mock LLM latency diagnosis. It identifies the broad one-shot Worker boundary, not internal model/import/IPC root cause or a production SLO.
- Step 7 and Step 8 crash evidence remains unchanged. Step 9 repeats one real Windows Job/FastEmbed crash under default recovery budgets: [application lifecycle](.local/unit1-step9-20261022/crash/application-lifecycle.json), [independent observations](.local/unit1-step9-20261022/crash/timeline.jsonl), [result](.local/unit1-step9-20261022/crash/result.json). Thirteen application events establish failed generation, negative liveness result, confirmed cleanup, one charged restart, replacement generation and READY. Harness checks confirm both process trees exited, Job/slot release and Supervisor shutdown. This crash fixture uses SQLite/ASGI/test LLM, distinct from the TCP/PostgreSQL session fixture.

## 6. Reproducible commands

Run from repository root unless noted. Use fresh evidence directories; recreate only disposable containers, and verify exact `SHOW data_directory` before using the recorded database URLs.

```powershell
# From backend:
.venv/Scripts/python.exe -B -m unittest discover -s tests -v
$env:ASTERIA_RECOVERY_TEST_URL='postgresql+psycopg://concurrency_test@127.0.0.1:11030/recovery_protocol'
$env:ASTERIA_RECOVERY_TEST_PGDATA='/tmp/asteria-concurrency-cross-step9-20261022/pgdata'
.venv/Scripts/python.exe -B -c "import sys,unittest;sys.path.insert(0,'tests');r=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(['test_workspace_delete_recovery','test_startup_recovery']));sys.exit(not r.wasSuccessful())"
# From repository root:
backend/.venv/Scripts/python.exe -m ruff check backend/app backend/tests
git diff --check
backend/.venv/Scripts/python.exe -B backend/tests/experiments/workspace_creation_concurrency.py --url postgresql+psycopg://concurrency_test@127.0.0.1:11030/workspace_concurrency_test --pgdata /tmp/asteria-concurrency-cross-step9-20261022/pgdata --output NEW_WORKSPACE_RESULT.json
backend/.venv/Scripts/python.exe -B backend/tests/experiments/session_chat_concurrency.py --url postgresql+psycopg://concurrency_test@127.0.0.1:55489/workspace_concurrency_test --pgdata /tmp/asteria-concurrency-cross-step9-chat-20261022/pgdata --output NEW_SESSION_DIRECTORY
```

Both TCP fixtures require a fresh public schema. Container IDs, ports and version are in `postgres-cleanup.json`; the databases no longer exist. The new writer tests are discovered normally; a focused run from `backend` is `.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_telemetry_writer.py -v`.

## 7. Limitations and backlog (non-blocking)

1. **Single-process, best-effort storage only.** Queue saturation, disk failure, process exit and rotation can lose records; counters are volatile. A stuck sink can stop telemetry progress while HTTP/recovery continue. No multi-process writer or durability claim.
2. **Worker-to-HTTP correlation remains absent.** Existing HTTP/Flight identities and Worker generation IDs retain their separate meanings.
3. **Root-cause detail is bounded.** Negative liveness does not prove manual termination; original exit codes and detailed cleanup checks are not recorded. Do not enable unrestricted stderr/exception logging to fill this gap. Runtime readiness still does not reflect Worker availability.

## 8. Acceptance decision and later commit summary

**PASS for Unit 1 in the stated single-process scope.** The event-loop blocker no longer reproduces; independent bounded queues, failure accounting, mixed storage/rotation, business and Worker recovery isolation are verified. Full backend regression passes, previously missing PostgreSQL coverage was exercised, relevant TCP concurrency fixtures passed, and prior FAIL evidence is preserved. No additional correctness blocker was identified.

Later commit summary: isolate HTTP/client and Worker telemetry behind one bounded fair writer; preserve event schemas; expose local drop/failure counters; add deterministic shared-storage/failure/rotation tests and updated acceptance evidence. No commit or push was performed. Stop here for Chat review; Unit 2 has not begun.
