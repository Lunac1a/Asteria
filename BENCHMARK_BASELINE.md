# Backend Benchmark Baseline — Step 0

Recorded 2026-10-23 (Australia/Sydney). **Preparation only; no benchmark executed.**

## Exact source identity

Branch: `main`. Phase 4 baseline (all accepted code, tests and reports):
**`5d6687a52054b5e229cc471465b83d418c0c8c4d`**.

| Commit | Scope |
|---|---|
| `0ad3b38143666274d759eed1376c21d7f4ccafad` | Offline paired backup/restore, schema/startup guards, tests and Sprint 1 report |
| `a36b84eb5356419809848586924a5f98a6e0a53d` | Windows/PostgreSQL CI, release checks, Next.js patch and Sprint 2 report |
| `5d6687a52054b5e229cc471465b83d418c0c8c4d` | Final Phase 4 acceptance and incident response runbook |

The following commit adds only this baseline document; its exact SHA is obtainable
with `git log -1 --format=%H -- BENCHMARK_BASELINE.md`. It has identical application,
dependency, test and configuration contents to the Phase 4 SHA above. At the start
of any future run, record its actual `git rev-parse HEAD` and diff, not only this
historical baseline. No push performed. Historical reports retain their original
HEAD/uncommitted-state descriptions; those describe their verification dates.

## Confirmed local environment versus future runtime

| Item | Confirmed evidence / value |
|---|---|
| OS | Python platform reports Windows 11, version 10.0.26300, SP0. CIM details unavailable in sandbox; no hardware capacity claim. |
| Python | Backend venv Python 3.12.10 |
| Dependencies | Every `==` requirement in `backend/requirements.txt` matches installed metadata; full list in that committed file |
| Key Python versions | FastAPI 0.136.0, Uvicorn 0.44.0, SQLAlchemy 2.0.49, psycopg 3.3.3, fastembed 0.9.0, openai 2.32.0, Ruff 0.15.11 |
| Requirement file SHA-256 | `ec5fdf23530fa56125cd38557316959bd6efb86bd7203423dc6136448104421d` (local bytes when accepted) |
| Frontend | next/eslint-config-next 16.4.1; react/react-dom 19.2.4; exact transitive graph in package-lock.json |
| Local Node/npm | 24.14.1 / 11.11.0; CI uses Node 22, not verified equivalent by remote execution |
| PostgreSQL | 18.6 in isolated release/incident evidence; no currently running target DB inspected or provisioned in Step 0 |
| Accepted PG image | `postgres@sha256:77f585114c32fbca283dc835b0596f4e52b51b4c6662d7810b2f4084f60a1873`; validate actual future image, PGDATA and database identity |
| API process | Supported single Windows process; `local.py serve` uses workers=1, loopback, limit_concurrency=16, keepalive=5s; a fixture may override these, so record actual runtime |

Complete installed requirement verification is local evidence at
`.local/benchmark-baseline-20261023/environment.json`; no environment secrets were
read or copied. Package-file hashes can differ across checkout line endings; Git
blob identity and locked versions are authoritative across machines.

## Configuration contract

These are source defaults in `backend/app/core/config.py`, **not a dump of the live
.env and not a claim that a future benchmark process has loaded them**.

| Setting | Default | Future run requirement |
|---|---|---|
| EMBEDDING_BACKEND / model | fastembed / sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 | Record cache/model identity and cold/warm preparation |
| PERSISTENT_QUERY_WORKER_ENABLED | false | Explicitly choose; accepted incident enabled true. Never silently compare one-shot versus Persistent Worker |
| Worker startup timeout | 30s | Confirm effective value and READY before requests |
| Restart policy | limit 3, backoff 1s, window 600s, failed-cycle limit 3 | Preserve bounds; budget is process-local, no restart-to-reset workaround |
| RUNTIME_METRICS_ENABLED | true | Confirm writer enabled, output root, drops/failures and rotation |
| SESSION_CHAT_CONCURRENCY_ENABLED | false | Freeze choice per run; no multi-process capacity claim |
| Singleflight | wait 30s, per-request follower limit 4, global limit 8 | Record effective configuration and role/path mix |
| SESSION_LOCK_WAIT_SECONDS | 1s | Record timeouts and failures, not only successful latency |
| QUERY_TIMEOUT_SECONDS / INDEX_TIMEOUT_SECONDS | 30s / 90s | Keep cleanup guarantees |
| STARTUP_RECOVERY_ENABLED / maintenance | false / false | No active recovery or maintenance during normal workload |
| RECOVERY_SINGLE_PROCESS_CONFIRMED | false | Attestation only when required by maintenance; not proof of exclusivity |
| LLM_BACKEND / PROVIDER_IS_SIMULATED | provider / false | A Mock run must explicitly allow only its local endpoint; do not use default external Provider URL |
| CITATION_AUDIT_ENABLED | false | Freeze per run; avoid introducing additional Provider calls |
| Retrieval | min_score .35, lexical rescue true | Record any fixture override and dataset/chunk/model identity |

Existing Mock fixtures return deterministic synthetic grounded JSON/citations from
a local HTTP server, with synthetic user credentials and encrypted synthetic Provider
key. Real Windows Provider subprocess can call this Mock. `LLM_BACKEND=test` is a
different execution path and must be labeled separately. No paid endpoint, key or
production data is part of the baseline. Future port, dataset size, concurrency,
prompt distribution, pool configuration, cache state and Mock service latency must
be chosen and recorded before measurements; they are not yet fixed by Step 0.

## Telemetry and existing fixtures

HTTP Runtime Metrics and Worker lifecycle share `LOCAL_DATA_DIR/runtime/metrics.jsonl`
under one bounded background writer. Record trace_id, http_request_id, business
request_id and flight_id distinctly. Stage intervals are inclusive; coverage is an
interval union and nested Provider time must not be added again. Unexplained time
has no assigned root cause. Events can drop/rotate and Worker generation is not
directly linked to HTTP identity; readiness alone does not establish Worker health.

Existing committed fixtures in `backend/tests/experiments/`:

- `chat_baseline.py`, `chat_worker_ab.py`, `chat_worker_ab_block.py`: existing Chat and Worker comparisons.
- `run_final_load.py`, `final_load_workload.py`, `analyze_final_load.py`: prior controlled workload and analysis.
- `workspace_creation_concurrency.py`, `session_chat_concurrency.py`, `chat_idempotency.py`, `chat_singleflight.py`: correctness boundaries.
- `query_supervisor_recovery.py`, `restart_policy.py`, `windows_worker_job.py`: reliability evidence, not throughput scores.

Inspect each fixture's exact database guards, CLI and settings before selecting it;
some use testing overrides. No fixture was run as a performance benchmark here.

## Verification and evidence preservation

Application source hashes match the accepted release snapshot manifest in
`.local/release-resume2-20261023/release-code-manifest.json`. No product behavior
was changed during commit preparation. Staging used explicit file lists, diff
reviews/checks and private-path exclusion; no `.env`, data, dumps, keys or `.local`
artifact was staged. Synthetic test constants and isolated CI passwords are not
production credentials. Path exclusion is not a general secret-content scanner.

Step 0 checks: Ruff passed; 10 focused backup guards passed (0.068s); staged diff
checks passed. Recent unchanged-source evidence reused, not rerun or summed:

- Sprint 2 full backend: 352 passed / 9 conditional skips; separate real PG lane:
  38 passed / 1 SQLite-only skip. These overlap.
- Frontend: npm ci, lint, 15 tests, production build and local page smoke passed
  after 16.4.1 patch; production audit zero at that time.
- Sprint 3: 38 focused tests; one actual Windows Job Worker crash recovered,
  HTTP Chat/data/cleanup checks passed.

References: `PHASE4_FINAL_REPORT.md`, `PHASE4_UNIT3_SPRINT1_REPORT.md`,
`PHASE4_UNIT3_SPRINT2_REPORT.md`, and `backend/docs/{windows-deployment,backup-restore,release-rollback,incident-response}.md`.
Local detailed evidence remains under `.local/` and pre-existing untracked
`backend/tests/experiments/evidence/`; deliberately not committed or deleted.
Tracked state is clean after the baseline commits; retained untracked evidence
means `git status` is not globally empty. Fresh clones will not contain those local
artifacts, so do not claim their availability from Git alone.

## Limits relevant to the Capstone

Phase 4 remains CONDITIONAL PASS for Windows/single-process/offline recovery.
No remote CI or production deployment validation; no Linux migration, arbitrary
schema downgrade, online backup, durable restart budgets or complete telemetry.
Five high dev-dependency audit entries remain in the braces chain (one root advisory),
as recorded in Sprint 2; keep CI isolated and do not disable audits or force unrelated
upgrades. No claim that all software is vulnerability-free.

Historical experiments used different configurations and do not establish a score
for this commit. Before Benchmark Step 1, freeze the actual run manifest, isolated
resources and correctness checks. This document does not authorize or start capacity
testing, tuning, production deployment or a Git push.
