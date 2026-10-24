# Provider execution path profiling

Date: 2026-10-24. **PASS within this fixed-request Windows diagnostic scope.** Fresh child imports and SDK initialization are the largest measured contributors. This is not a capacity result or an optimization verification.

## Environment and source identity

- Branch `main`, HEAD `8b1b0d9b605d25e7e6d6101bf21a60708aa33135`; tracked diff empty before and after. No production files changed, commits or pushes. Earlier untracked benchmark tools/reports and evidence remain intact.
- Windows 11 build 26300, Python 3.12.10, Intel i9-13980HX (24 cores / 32 logical processors), 33,954,312,192 bytes physical RAM. Shared development host, no exclusive CPU allocation.
- Real single-process FastAPI 0.136.0 TCP server; isolated PostgreSQL 18.6; OpenAI SDK 2.32.0, HTTPX 0.28.1, FastEmbed 0.9.0. Full dependency versions: `run/source.json` below.
- S1 enabled, Persistent Query Worker enabled, runtime metrics enabled, actual Provider subprocess enabled. Calls are sequential independent sessions, not concurrent load. Existing admission, timeout, restart and cleanup policies unchanged. Provider timeout cap 45 seconds; cleanup bound 5 seconds; HTTP timeout 90 seconds; SDK retries zero and redirects disabled.
- Frozen D1 SHA256 `178365eac1181925ffaed23b2a919c3b5f99f2c8a385f58ed5823216fdc9aa77`: 20 documents, 200 real indexed chunks, 384-dimensional vectors, backend-qualified model `fastembed:sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`. Vector identity matched the accepted C1 preflight.
- Local deterministic Mock HTTP LLM retains the existing 200 ms configured delay. No paid calls. Query `q15-0` repeated against distinct pre-created sessions with unique request identities.

Evidence root: [`.local/provider-profile-20261024-02`](.local/provider-profile-20261024-02/). `freeze.json` records production/tool SHA256 values, fixed order, clocks and limits; `run/source.json`, `configuration.json`, `postgres.json`, `model-manifest.json`, `indexed-manifest.json`, `dataset.json`, and `hardware.json` record actual identities. `source-integrity.json` confirms every frozen file still matches. Generated profiling sources also have hashes there.

## Method and verification

Reviewed earlier parent-duration/Job CPU evidence before adding profiling. The old `backend/tests/experiments/provider_concurrency.py` includes barrier/release waiting and a different workload; its wall time is not directly comparable. Embedding import profiles describe another subprocess and cannot establish Provider costs.

Added only benchmark tools: `provider_profile_hooks.py`, `provider_profile_measure.py`, `provider_profile_run.py` under `backend/tests/experiments/`, and `backend/tests/test_provider_profile.py`. The harness generates a temporary marked copy of the real Provider entrypoint, preserving its original backend import root. The marked process function retains the original lock/quarantine references, native Job launch, IPC, deadlines and cleanup code. All SDK execution and native containment remain real. Control calls use the original child and process function.

After 60/60 full D1 RAG checks, executed the predeclared 12-call order `(control, profile, profile, control) × 3`: six controls and six profiled calls. Host/model/filesystem caches are warm; each Provider still starts a fresh Python child. No requests were retried, omitted or added to compensate for outcomes.

All 12 calls returned HTTP 200 with correct oracle/citations and persisted database state. Exactly 12 measured Provider calls; 72 including warmup. Checked each measured ChatTurn, message/evidence records, expected total message count (864), untouched sessions and stable non-Chat table hashes. Measured HTTP telemetry has 12 records; HTTP/lifecycle writer dropped/failed/queued counters were zero. See `run/result.json`, `verified-requests.json`, `measured-telemetry.json`, `mock-calls.json` and `measurement.json`.

Each measured Provider Job reported zero active processes at cleanup and three total OS processes (including launchers, not three LLM calls). Worker stopped, all three recorded Worker processes exited, Job and storage lease released, API exited 0 and experiment PostgreSQL was removed. Evidence: `run/api-after.json`, `api-cleanup.json`, `result.json`; independent Docker name lookup also returned no matching container. Existing Docker Desktop was left running.

Initial directory `provider-profile-20261024-01` preserves an environment-only NOT READY result: Docker Desktop was stopped; no queries or Provider calls ran. Started the existing Docker Desktop, verified the frozen image, then used a fresh directory. No previous evidence was replaced.

## Results and profiling overhead

All durations below are milliseconds. Six observations per mode; descriptive medians only, no percentile claims.

| Provider measurement | Original control | Profiled |
|---|---:|---:|
| Mean wall time | 1642.582 | 1650.747 |
| Descriptive median | 1634.935 | 1646.415 |
| Minimum–maximum | 1614.33–1680.98 | 1632.95–1679.46 |
| Mean whole Provider Job CPU | 1252.604 | 1252.604 |

Profile mean +0.497%, median +0.702%: both satisfy the predeclared absolute 5% material-distortion gate. This small alternating comparison supports limited distortion in these calls, not statistical equivalence. Parent hooks exist in both groups; profile-file writing and child finalization are included in measured wall time. Mean whole HTTP server duration across both groups was 1803.803 ms; generation 1652.828 ms, material acquisition 105.349 ms, unexplained HTTP time 8.071 ms. These HTTP stages contain the Provider timer and must not be added to it.

## Lifecycle timeline and CPU

The following is the mean of six profiled request timelines, relative to Provider entry. Each boundary is backed by per-request timestamps in `timeline.json`; consecutive boundaries partition wall time without adding concurrent waits.

| Boundary | Offset ms |
|---|---:|
| Provider entry | 0.000 |
| `run_provider` entry | 1.013 |
| Job creation begin / end | 1.075 / 1.143 |
| Atomic launch begin | 1.146 |
| First child Python marker | 114.133 |
| Final child marker | 1570.732 |
| Parent stdout reaches EOF | 1645.199 |
| Explicit cleanup begin / end | 1645.691 / 1650.681 |
| `run_provider` return | 1650.701 |
| Provider return | 1650.761 |

The outer wrapper differs from runtime Provider telemetry by less than 1 ms in every call (mean rounding/wrapper difference about 0.014 ms). Configuration lookup, URL validation, credential decoding and transaction release in `completion._provider_completion` precede the Provider timer: measured combined mean 3.212 ms, not included above.

Child intervals below are inclusive and sequential, except explicitly nested HTTP. CPU is child process CPU delta; zero means no counter increment observed, not proof of zero work.

| Child interval | Mean wall | Mean CPU |
|---|---:|---:|
| Initial standard-library imports/bootstrap after first marker | 40.605 | 36.458 |
| Protocol import, including transitive imports | 350.336 | 309.896 |
| HTTPX import | 41.676 | 36.458 |
| OpenAI import | 351.571 | 322.917 |
| Runtime metrics / process imports | 3.451 | 0.000 |
| Memory-limit module import / installation | 1.342 | 0.000 |
| Child stdin read / input JSON decode | 0.159 | 0.000 |
| HTTPX/OpenAI client initialization | 183.453 | 171.875 |
| Lazy `client.chat.completions.create` attribute resolution | 192.132 | 179.688 |
| SDK create, inclusive | 291.792 | 49.479 |
| ↳ HTTP send and body read, nested in SDK create | 215.026 | 0.000 |
| Application response validation (`decode_response`) | 0.012 | 0.000 |
| SDK context close/unwind | 0.010 | 0.000 |
| Output JSON encoding / buffered print | 0.020 | 0.000 |

Whole marked child lifetime: **1456.599 ms wall / 1106.771 ms CPU**. Observer installation, definitions and inter-marker residual are recorded in `analysis.json`; residual is about 0.026 ms. Whole Job CPU includes child CPU; do not add them. About 145.833 ms Job CPU is outside the marked child interval (other launchers and pre/post-marker child execution combined). Parent API-thread CPU averages 7.813 ms and is separate; IPC-thread counters are separately recorded. CPU samples commonly advance in 15.625 ms steps despite finer advertised clock resolution.

Source boundaries: `backend/app/services/llm/provider.py:11` imports; `:19` decorated Provider entry; `:78` SDK request; `:96` application response validation; `:119` child memory guard and stdin/output path. `backend/app/services/llm/process.py:15` owns the operation; `:50` launches; `:54` runs IPC; `:68` waits; `:81` starts cleanup; `:108` closes Job. `backend/app/services/llm/protocol.py:5` imports FastAPI transitively. That source dependency explains why “protocol import” is not merely the small protocol file, but does not allocate time to individual transitive modules.

## Overlap and unknown time

- Parent `job.launch` averages 12.606 ms, contained within the 112.987 ms launch-begin → first-child-marker interval. The remaining startup time is not separately attributed to interpreter, virtualenv launcher, OS or security software.
- Parent stdin write takes 889.171 ms, overlapping child bootstrap/imports before the child reads stdin. It is blocking backpressure, not an additive 889 ms serialization cost. Stdout read averages 741.961 ms; mailbox wait 1631.261 ms overlaps both IPC operations and child execution. None is added to the child intervals.
- SDK create excludes 215.026 ms HTTP and leaves 76.766 ms combined request preparation/SDK bookkeeping/parsing. Only 2.911 ms falls after full HTTP body read and before create returns. The attempted `LegacyAPIResponse.parse` hook produced **no marks**: installed OpenAI `_base_client.py:1169–1181` uses `APIResponse.parse` on this path. Exact SDK parsing time is therefore **not measured**, not zero; it is contained in that 2.911 ms post-HTTP interval. The offline analyzer was corrected to preserve this gap instead of inventing a parse duration. No execution was repeated to improve results.
- Final child marker → stdout EOF is 74.467 ms, including profiling-file write, buffered stdout flushing and interpreter/launcher shutdown. These components cannot be separately assigned. Explicit owned-process verification/Job cleanup is measured independently at 4.990 ms (range 2.373–16.014).
- Cross-process wall markers use the same Windows QueryPerformanceCounter reference and ordering was checked on every profile. CPU clocks are compared only within their process/thread; neither CPU time nor nested wall intervals are added as extra latency.

## Supported finding and one optimization hypothesis

Fresh-process imports account for about **789 ms (48% of Provider wall)**; SDK/client and lazy resource initialization add **376 ms (23%)**. Together they account for about 71%. CPU evidence supports substantial local initialization work. Mock HTTP occupies about 13%; explicit Job creation and cleanup are small. This identifies useful execution boundaries, not a cause inside Windows, the database, an individual transitive import, or the real remote LLM service.

**One hypothesis for subsequent review:** reduce unnecessary transitive imports in the standalone Provider bootstrap while preserving the same per-call subprocess, Job ownership, memory guard and fail-closed cleanup. The 350 ms protocol import boundary is a concrete candidate to investigate; its entire duration is not assumed removable. No optimization has been implemented or benefit claimed.

## Reproduction and checks

From repository root, use a fresh output directory and preserve the accepted C1 preflight directory:

```powershell
backend/.venv/Scripts/python.exe -B backend/tests/experiments/provider_profile_run.py --output .local/provider-profile-NEW/run --measure-gate .local/benchmark-c1-complete-20261023-01/preflight
backend/.venv/Scripts/python.exe -B .local/provider-profile-20261024-02/analyze.py
backend/.venv/Scripts/ruff.exe check backend/tests/experiments/provider_profile_hooks.py backend/tests/experiments/provider_profile_measure.py backend/tests/experiments/provider_profile_run.py backend/tests/test_provider_profile.py
git diff --check
```

The retained analyzer targets this evidence directory; change its input root for future evidence without overwriting this run. From `backend/`:

```powershell
.venv/Scripts/python.exe -B -m unittest discover -s tests -p 'test_provider*.py'
```

Results: 3 focused profiling tests before execution; final Provider suite **12 passed**, including those same three (do not sum). Ruff on all four new files and `git diff --check` passed. An initial regression command from repository root failed two imports because `app` was not on the import path; corrected backend working-directory run passed. Both outputs preserved. The freeze contains inherited historical tool-test/overload notes from Step 5; they are provenance, not additional tests or a claim that arrival-load conditions were run here.

Verdict: **PASS** for identifying the major Provider cost boundaries with correct real RAG execution and limited observed profiling distortion. Exact SDK parsing, pre-marker startup and finalization remain bounded gaps. No capacity, persistent-Provider safety, production optimization or higher-concurrency guarantee follows from this exercise.
