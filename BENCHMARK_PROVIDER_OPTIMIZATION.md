# Provider bootstrap import optimization

2026-10-24 — **PASS**, limited to the tested Windows single-process, sequential D1 RAG workload. One deferred import reduced real Provider and end-to-end Chat latency without changing execution or recovery policies.

## Source identity and change

Branch `main`, HEAD `8b1b0d9b605d25e7e6d6101bf21a60708aa33135`. Before this task there were no tracked modifications; previous benchmark tools, reports and evidence were untracked/ignored and remain preserved. The only production change is `backend/app/services/llm/protocol.py`: remove the module-level `from fastapi import HTTPException` and import it inside `failure()` instead (3 added lines, 1 removed). No commits or pushes.

The actual chain was `llm/provider.py:11` → `llm/protocol.py` → FastAPI and its transitive dependencies. Provider execution needs `GenerationError` and `GenerationText`; neither needs FastAPI. Only `protocol.failure()` (`:20`) translates generation errors into HTTP errors for API callers. No repository caller imports `HTTPException` from this module. The public generation types, schemas, parsing functions, error categories, HTTP status/details and response format remain unchanged.

This function-local import separates HTTP translation from standalone protocol initialization without creating another module or moving public symbols. FastAPI remains a required API dependency; normal API execution already imports it. The Provider retains its per-call subprocess, real Windows Job assignment, memory guard, IPC limit, deadline, redirect/retry settings, cleanup verification and fail-closed quarantine. Those production files are unchanged.

Original protocol SHA256: `7f6d9d5af32e213f1aea0d05f843ff784cc4798485d0e7f192a6c153c9e7cec6`.
Optimized protocol SHA256: `3a5afe7324cbfcb8338635117857844bca8983165c980f1121693612a6e411ae`.
The original snapshot was verified against HEAD, and the optimized snapshot against the sole intended import change.

## Controlled comparison

Evidence: [`.local/provider-optimization-20261024-01`](.local/provider-optimization-20261024-01/). `freeze.json` was written before execution and records both snapshots, tool hashes, order and acceptance gates. `integrity.json` verifies the snapshots, dependency/configuration equivalence and profiling-output allowlist. Original Step 6 evidence is unchanged.

- Windows 11 build 26300; Intel i9-13980HX, 24 cores / 32 logical processors; Python 3.12.10. Shared development host, no exclusive CPU reservation.
- Same installed dependency set as Step 6, including FastAPI 0.136.0, OpenAI 2.32.0, HTTPX 0.28.1 and FastEmbed 0.9.0. Actual versions are in `run/source.json`; CPU identity in `hardware.json`.
- Real FastAPI TCP, isolated PostgreSQL 18.6, offline FastEmbed, real Persistent Query Worker, actual Provider subprocess and Windows Jobs, deterministic local Mock HTTP with the unchanged 200 ms delay. S1 enabled, one independent-session request at a time. Configuration matches Step 6 except isolated ports/storage paths.
- D1 SHA256 `178365eac1181925ffaed23b2a919c3b5f99f2c8a385f58ed5823216fdc9aa77`; 20 documents, 200 indexed chunks, 384-dimensional vectors, qualified model `fastembed:sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`. Vector identity matched accepted C1 evidence.
- All 60 warmup/preflight RAG queries passed before measurement. Then fixed query `q15-0`, distinct pre-created sessions and unique request identities, no retries. Dataset/Worker caches warm; every Provider gets a fresh child.

Predeclared order, repeated three times:

`original_control, optimized_control, original_profile, optimized_profile, optimized_profile, original_profile, optimized_control, original_control`

Exactly 24 measured calls: six in each cell. Control means no child/internal lifecycle markers; it still has the shared outer observation and resource accounting. Both versions use an identical file-loading shim to load their frozen protocol under its canonical module name. The same real Provider source is used in both; only the protocol snapshot differs. The API parent uses the optimized protocol throughout, which does not change the already-imported API framework path.

New benchmark-only files are `backend/tests/experiments/provider_ab_hooks.py`, `provider_ab_measure.py`, `provider_ab_run.py`; they preserve the old profiling tools and reuse the frozen TCP/PostgreSQL fixture. Temporary child sources live under `run/profiling/` and their hashes are retained. `backend/tests/test_provider_bootstrap.py` adds focused import/translation/tool checks.

## End-to-end results

All values below are milliseconds unless stated otherwise. Six observations per cell; medians are descriptive, not percentile estimates.

| Unprofiled metric | Original | Optimized | Mean change |
|---|---:|---:|---:|
| Provider mean wall | 1462.732 | 1351.627 | −111.105 / −7.60% |
| Provider median wall | 1469.580 | 1348.260 | — |
| Provider min–max | 1420.49–1511.58 | 1286.50–1419.09 | — |
| Chat server mean | 1619.488 | 1499.048 | −120.440 / −7.44% |
| Chat server median | 1598.515 | 1487.065 | — |
| Client HTTP mean | 1619.833 | 1497.500 | −122.333 / −7.55% |
| Whole Provider Job CPU mean | 1135.417 | 1013.021 | −122.396 / −10.78% |
| Peak Job memory mean, MiB | 60.005 | 54.025 | −9.97% |
| Peak individual process memory mean, MiB | 55.317 | 49.340 | −10.81% |

Memory comes from Windows Job extended-limit peak accounting before handle closure, rather than treating sparse RSS samples as peaks. Job memory is committed-memory accounting, not resident memory. Job CPU includes its launchers and child; child CPU is a subset, not additional CPU. All measured Jobs contained three total OS processes, zero active at cleanup; these are not three LLM executions.

Each block contains two unprofiled calls per version:

| Block | Original Provider mean | Optimized Provider mean | Original server mean | Optimized server mean |
|---|---:|---:|---:|---:|
| 1 | 1451.745 | 1339.020 | 1598.515 | 1478.665 |
| 2 | 1466.870 | 1352.795 | 1611.460 | 1517.470 |
| 3 | 1469.580 | 1363.065 | 1648.490 | 1501.010 |

Both predeclared benefit conditions passed: unprofiled Provider and server means **and** medians decreased. Every block has the same direction. Raw per-call metrics: `analysis.json`; block aggregates: `blocks.json`; HTTP/oracle/DB evidence: `run/verified-requests.json` and `measured-telemetry.json`.

## Import attribution, CPU and overlap

Mean profiled child intervals, shown as wall / child CPU milliseconds:

| Interval | Original | Optimized |
|---|---:|---:|
| Protocol import, including identical snapshot loader | 283.423 / 278.646 | 2.117 / 7.812 |
| HTTPX import | 33.989 / 31.250 | 89.292 / 83.333 |
| OpenAI import | 294.311 / 294.271 | 423.654 / 416.667 |
| Runtime metrics import | 1.833 / 2.604 | 1.760 / 2.604 |
| Process module import | 2.611 / 2.604 | 2.588 / 5.208 |
| SDK/client initialization | 160.906 / 156.250 | 161.920 / 153.646 |
| Lazy SDK resource initialization | 162.370 / 166.667 | 165.039 / 169.271 |
| SDK create, inclusive | 274.634 / 52.083 | 283.926 / 62.500 |
| ↳ HTTP send/body read, nested | 212.891 / 2.604 | 213.576 / 5.208 |
| ↳ SDK response parse, nested | 2.423 / 5.208 | 2.560 / 0.000 |
| Application response validation | 0.013 / 0.000 | 0.012 / 0.000 |

The five named import intervals total **616.167 → 519.411 ms**, a net reduction of **96.756 ms**. Removing the eager FastAPI import avoids about 281 ms at that boundary, but about 185 ms moves into HTTPX/OpenAI imports. Shared transitive dependency initialization is delayed, not all eliminated. The net import reduction, lower whole-Job CPU/memory, and unprofiled whole-request results support the specific optimization; the isolated protocol-import difference alone would overstate it.

Whole marked child lifetime: **1248.621 → 1165.746 ms wall**, **1018.229 → 937.500 ms CPU**. Whole profiled Provider: **1441.632 → 1344.823 ms**. Remaining intervals include launch-begin → first child marker **111.878 → 109.764 ms**, last child marker → stdout EOF **71.591 → 65.040 ms**, and explicit Job cleanup **8.043 → 2.776 ms**. Those smaller differences are observations, not claimed effects of the import change.

Parent stdin write **749.476 → 650.987 ms** overlaps child imports while waiting for the child to read. Mailbox/pipe waits also overlap child execution. They are not added to import/SDK wall time. SDK HTTP and parse intervals are nested inside SDK create, and the Provider timer is nested inside Chat generation. CPU is reported separately from wall time; coarse CPU counter increments explain zero/small interval CPU anomalies.

This experiment corrects the benchmark-only SDK parse hook to the installed `APIResponse.parse` path in both profiled arms. Every expected mark was present and unique. Parent/child wall markers use QueryPerformanceCounter; order and containment were verified. Parent wrapper and Runtime Metrics Provider duration agree within 1 ms for every request. Initial interpreter/launcher work and post-marker finalization remain inclusive gaps; the roughly 33–34 ms child residual includes standard-library bootstrap/definitions outside the listed intervals. No gap is assigned to a specific OS or dependency internals without evidence.

## Profiling overhead

Profile versus control Provider wall difference within each version:

| Version | Mean difference | Median difference |
|---|---:|---:|
| Original | −1.443% | −2.011% |
| Optimized | −0.503% | −0.746% |

Both satisfy the predeclared absolute 5% gate. Negative differences reflect small-sample/order/host variation, not beneficial instrumentation. This is not a statistical equivalence test. The performance conclusion uses unprofiled full Provider/Chat measurements, not import microbenchmarks. The common snapshot loader and outer observations remain limitations of the controlled comparison.

## Correctness, safety and regression

- 60/60 preflight and 24/24 measured requests passed real TCP response, citation/evidence oracle and PostgreSQL checks. Exactly 24 measured Provider calls (84 including warmup), no rejection/timeout/transport failures or retries.
- Every measured request has one correct committed ChatTurn, correct message/evidence records and response hash. Totals: 84 ChatTurns, 84 evidence records, 888 messages. Unused sessions and non-Chat table hashes remained unchanged.
- All 24 HTTP telemetry records and Provider attributions matched. HTTP/lifecycle dropped, failed and queued counters were zero at completion. Profiling JSON contains fixed marker names, numeric times/resource values and opaque request IDs only; no payload, credentials, exception text or private content is captured. Synthetic response evidence remains in the local fixture directory as before.
- Worker remained READY without restart during execution. Shutdown: Worker STOPPED, all three recorded Worker processes exited, Job and storage lease released, API exit 0. PostgreSQL removed and independently confirmed absent (`cleanup-independent.json`). No unrelated container was removed.
- **15 Provider tests passed**, including the new three tests: fresh-process protocol import with FastAPI/Starlette explicitly forbidden; HTTP error category/status translation; A/B ordering and retained safety code. Existing tests cover timeout classification, credential exclusion from argv, no redirects/retries, bounded blocked stdin, atomic Job assignment failure, and fail-closed cleanup uncertainty.
- **24 Chat tests passed** on a bounded elevated rerun: capacity, Singleflight/followers and stage timing/exception behavior. The initial restricted run stalled before output and was stopped; its result is not counted as a pass. Only the two identified test processes were stopped. The rerun completed in 3.556 seconds; exact cause of the initial environment stall is unconfirmed.
- **33 generation-reliability tests passed**, covering error/protocol/Chat generation behavior. These unit/API tests use their existing isolated synthetic configuration; they do not substitute for the real PostgreSQL A/B run.
- Full-backend Ruff and `git diff --check` passed. Previous frozen benchmark/production hashes (other than the newly targeted protocol) still match. No concurrency, timeout, containment or recovery limit changed.

Evidence files: `tool-tests.txt`, `chat-tests.txt`, `chat-tests-retry.txt`, `generation-tests.txt`, `ruff.txt`, `run/result.json`, `run/api-after.json`, `run/api-cleanup.json`, `integrity.json`, `git-before.txt`, `git-after.txt`.

## Reproducible commands and limits

From repository root, first prepare a **new** evidence directory with the retained original and optimized protocol snapshots, and freeze their hashes/configuration/order before running:

```powershell
backend/.venv/Scripts/python.exe -B backend/tests/experiments/provider_ab_run.py --output .local/provider-optimization-NEW/run --measure-gate .local/benchmark-c1-complete-20261023-01/preflight
backend/.venv/Scripts/python.exe -B .local/provider-optimization-20261024-01/analyze.py
backend/.venv/Scripts/ruff.exe check backend
git diff --check
```

The analyzer targets the retained evidence directory; adapt its root for a new run without overwriting this evidence. It enforces the fixed count, frozen hashes, correctness and interval checks. From `backend/`:

```powershell
.venv/Scripts/python.exe -B -m unittest discover -s tests -p 'test_provider*.py'
.venv/Scripts/python.exe -B -c "import faulthandler,unittest; faulthandler.dump_traceback_later(90,exit=True); unittest.main(module=None,argv=['unittest','discover','-s','tests','-p','test_chat*.py','-v'])"
.venv/Scripts/python.exe -B -m unittest discover -s tests -p 'test_generation_reliability.py'
```

**PASS:** the minimal change provides a consistent measured benefit while tested business/safety contracts remain intact. No p95/p99, maximum-capacity, concurrency scaling, production traffic or paid-Provider claim is supported by six samples per cell. Current original-control latency differs from historical Step 6 (~1.64 s); only contemporaneous interleaved comparisons establish this benefit. No additional optimization or capacity experiment was performed.
