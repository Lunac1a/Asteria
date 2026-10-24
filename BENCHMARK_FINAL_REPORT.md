# Asteria Backend Engineering Benchmark Capstone

Finalized 24 October 2026. **Engineering investigation complete within the verified Windows single-process scope.** The Provider optimization improved latency and CPU cost, but the final fixed-load comparison demonstrated **no capacity benefit**. No new performance experiments were run during finalization.

## 1. Executive summary

The research question was whether independent-session RAG Chat requests could complete more work under controlled concurrent load, which execution boundaries limited progress, and whether a measured bottleneck could be reduced without weakening reliability.

The investigation used real TCP FastAPI, isolated PostgreSQL 18.6, offline FastEmbed, a Persistent Query Embedding Worker and a real Windows-isolated Provider subprocess calling a deterministic local Mock LLM. It checked every submitted request against database state and an evidence oracle.

Three findings emerged:

1. **Admission policy strongly shaped the observed workload.** At 1 req/s, enabling the existing S1 independent-session path increased mean window goodput from 0.5000 to 0.9889 req/s in that controlled comparison. This was not an application optimization or proof of maximum capacity.
2. **Repeated child initialization was a substantial Provider cost.** Profiling separated imports, SDK initialization, HTTP, IPC waits and cleanup. Moving an unnecessary eager FastAPI import into the API-only error translator reduced real request latency while retaining subprocess-per-call isolation.
3. **Lower latency did not necessarily admit more requests.** In the final contemporary A/B, Provider mean fell from **1623.98 to 1492.16 ms (−8.12%)**, successful Chat server mean from **1766.80 to 1635.38 ms (−7.44%)**, and Provider CPU per call by **10.30%**. Both versions still achieved **0.9917 successful req/s** with **33.33% HTTP 429** at 1.5 scheduled req/s.

These are results for a fixed synthetic workload on one Windows machine with a local Mock. They do not establish production throughput, real-model latency, Linux compatibility or maximum sustainable capacity.

## 2. System architecture

The measured path is:

```text
TCP HTTP → authentication and ownership → session/workspace coordination
         → Chat admission → routing → retrieval/material acquisition
         → isolated Provider generation → PostgreSQL persistence + commit
         → cleanup → completed HTTP response
```

- **API and coordination:** one Windows FastAPI/Uvicorn process. The default S0 path retains exclusive Chat admission; S1 permits two independent-session executions through the existing shared-admission policy. Session locks, workspace lifecycle permits and Singleflight remain separate mechanisms. The measured workload uses unique sessions/request IDs and fresh leaders, not duplicate-request fan-out.
- **Retrieval:** documents and chunk vectors reside in PostgreSQL. The application reads eligible chunks, embeds the question through the Persistent Query Worker and scores candidates in application code. This is not a PostgreSQL vector-index benchmark.
- **Embedding Worker:** the existing Supervisor manages a persistent FastEmbed process tree under a Windows Job Object, including bounded recovery, single-inflight embedding and fail-closed cleanup. Those policies were not changed.
- **Provider:** each generation launches a fresh Python subprocess in an owned Windows Job. The child receives its request through bounded IPC, initializes the OpenAI/HTTPX client, calls the local Mock, validates the response and exits. Timeouts, process-tree termination and cleanup verification remain enforced.
- **Persistence and evidence:** successful requests commit a ChatTurn, user/assistant Messages and matching MessageEvidence. Runtime Metrics records HTTP identity, execution source, first-level stages and nested Provider durations; lifecycle events describe Worker state. These signals complement database verification rather than replace it.

Relevant source: [Chat route](backend/app/api/routes/chat.py), [admission](backend/app/services/chat/capacity.py), [retrieval](backend/app/services/knowledge/retrieval.py), [Supervisor](backend/app/services/knowledge/query_supervisor.py), [Provider](backend/app/services/llm/provider.py), [process ownership](backend/app/services/llm/process.py), [Windows Job implementation](backend/app/services/knowledge/windows_job.py).

## 3. Benchmark methodology

### Environment and workload

| Control | Verified setting |
|---|---|
| Host | Windows 11 build 26300; Intel i9-13980HX, 24 cores / 32 logical processors; approximately 31.62 GiB RAM; shared development machine |
| Runtime | Python 3.12.10; FastAPI 0.136.0; Uvicorn 0.44.0; SQLAlchemy 2.0.49; psycopg 3.3.3; FastEmbed 0.9.0; OpenAI 2.32.0; HTTPX 0.28.1 |
| Database | Fresh isolated PostgreSQL 18.6 per repetition; fsync and synchronous_commit on; read committed; max_connections 100; shared_buffers 128 MB; work_mem 4 MB |
| API | Real loopback TCP, one process, Uvicorn concurrency limit 16, keepalive 5 seconds |
| Data | D1-v1, seed 20261023; 20 synthetic 8,000-character documents; 10 indexed chunks per document; 60 fixed queries and evidence/answer oracles |
| Embeddings | Offline `fastembed:sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`; 384 dimensions; content/vector/model manifests checked |
| Mock | Deterministic grounded JSON with citations, configured minimum 200 ms wait; received evidence checked before producing an answer |
| State | Fresh database, storage, API and Worker lifecycle per repetition; fixed initial session history; 60 non-measured real RAG queries before measurement |
| Safety | No retries, bounded outstanding requests, existing admission/timeouts/restart policies, memory/disk/evidence guards, verified cleanup before the next repetition |

D1 SHA256: `178365eac1181925ffaed23b2a919c3b5f99f2c8a385f58ed5823216fdc9aa77`. PostgreSQL image: `postgres@sha256:77f585114c32fbca283dc835b0596f4e52b51b4c6662d7810b2f4084f60a1873`.

Persistent Worker and Runtime Metrics were enabled throughout the main workload. S0/S1 was explicit per experiment; citation audit, startup recovery and maintenance were disabled. Each repetition recorded actual settings, installed versions, source/tool hashes, indexed vectors and resource observations. Ports, generated IDs and isolated storage paths naturally differed.

The initial [workload design](BENCHMARK_WORKLOAD_DESIGN.md) proposed a broader matrix. Only subsequently authorized cells were executed. Later fixed-arrival experiments explicitly used periodic schedules and client bounds of two or four, rather than treating every candidate in the design as executed or supported.

### Metric and correctness definitions

- **Offered:** scheduled arrivals; **sent:** attempts actually dispatched; **admitted:** observed successful Chat admission. These counts are not interchangeable.
- **Window goodput:** HTTP responses completed inside the original measurement window and later verified correct, divided by that window's duration. Successful drain completions remain successful outcomes but do not increase window goodput.
- **Rejection rate:** rejected/sent, with offered and not-sent counts retained. Timeouts, transport errors and 503s remain explicit; a client timeout is not proof of server cancellation.
- **Client latency:** send to complete response-body receipt, excluding subsequent JSON/oracle/hash/database verification. All-status and successful/rejected distributions are separate.
- **Server/stage timing:** inclusive monotonic intervals. Coverage is their interval union; nested Provider time is not added to Generation. Unexplained time is retained without attributing it to an unmeasured component.
- **CPU and memory:** CPU seconds are separate from elapsed wall time. Provider CPU comes from complete Job accounting; sampled RSS/private bytes are observations, not exact exclusive-memory peaks. Missing samples are not zeros.
- **Percentiles:** nearest-rank p50 requires n≥20, p95 n≥200 and p99 n≥1000. Small-sample medians are descriptive. Repetitions and incompatible scenarios are not pooled to manufacture tail statistics.

Every submitted request was reconciled with PostgreSQL, response/evidence oracles and Provider attribution. Preparation, indexing, warmup, measurement, drain and offline verification have separate boundaries. Historical failed preflights remain preserved and are not counted as accepted runs.

## 4. Capacity and concurrency findings

| Experiment | Observed result | Interpretation |
|---|---|---|
| S0, closed-loop C=1, three 120-second windows | 69/69/74 total successes; 68/68/73 in-window; goodput 0.5667/0.5667/0.6083 req/s; no rejection | Valid baseline for this sequential workload, not maximum capacity |
| S0, closed-loop C=2 | Each repetition reached the 300-request cap in 3.344–3.453 seconds: 2 successes, 298 HTTP 429s; one success in-window | A rapidly rejected client lane generated a rejection-heavy burst. Short windows and tiny success samples do not support a steady-state scaling claim |
| Fixed 1 req/s, maximum 2 outstanding, S0 versus S1 | S0: 0.5000 req/s in all three runs. S1: 0.9833/0.9917/0.9917; zero server rejection, but one client not-sent arrival in the first S1 run | S1 improved goodput at this specific controlled point; the client-bound event was preserved rather than hidden |
| S1, fixed 1.5 req/s, maximum 4 outstanding | Each historical repetition: 119 in-window successes, 120 total successes, 60 admission 429s, no client drop | The observed first rejecting boundary was Chat admission; increasing offered load did not increase observed goodput beyond approximately 0.99 req/s |

The 1 and 1.5 req/s experiments changed both offered rate and client outstanding bound, so their difference is not a matched capacity A/B. Similarly, C=1 and the cap-limited C=2 burst do not share equivalent measurement windows. The final Provider A/B below fixes these factors and is the appropriate comparison for the optimization.

The two-slot admission limit and periodic arrival spacing can restrict observed completions before a hardware limit is established. No saturation curve, production SLO or maximum sustainable arrival rate was verified. Candidate C=4/8/16 levels and other scenarios in the design were not implicitly accepted.

Supporting reports: [C=1](BENCHMARK_C1_BASELINE_REPORT.md), [C=2](BENCHMARK_C2_REPORT.md), [S0 versus S1](BENCHMARK_S0_S1_COMPARISON.md), [S1 at 1.5 req/s](BENCHMARK_S1_1_5RPS_REPORT.md).

## 5. Bottleneck investigation

Runtime Metrics showed Generation/Provider dominating successful request latency. Parent-stage timing alone could not distinguish remote HTTP work from process startup, imports, IPC or cleanup, so the investigation profiled the real child/Job path.

The initial six-profile/six-control diagnostic measured approximately 1.65 seconds per Provider call. About **789 ms** occurred in child import boundaries and **376 ms** in SDK/client and lazy resource initialization, versus about **215 ms** in the Mock HTTP exchange. Explicit Job cleanup averaged about **5 ms**. Profiled mean wall time was 0.5% above controls, within the predeclared 5% mean/median difference gate; this small comparison was not a statistical equivalence test.

These intervals required careful interpretation. Blocking stdin writes overlapped child imports, and mailbox/stdout waits overlapped execution; they were not additional latency. HTTP and SDK parsing were nested inside SDK create. Child CPU was already included in whole-Job CPU. Interpreter startup before the first marker and finalization after the last marker remained inclusive gaps.

An attempted legacy SDK parsing hook did not fire in the initial diagnostic and was reported as missing, not zero. The subsequent optimization experiment used the installed SDK's actual `APIResponse.parse` path symmetrically in both profiled arms. No internal database or Windows root cause was inferred from a parent duration alone. See [Provider profiling](BENCHMARK_PROVIDER_PROFILING.md).

## 6. Minimal optimization and safety

`llm/provider.py` imported `GenerationError` and `GenerationText` from `llm/protocol.py`. That protocol module eagerly imported FastAPI's `HTTPException`, although only the API-facing `failure()` translator needed it. The Provider child did not call that translator.

The production change removes the module-level FastAPI import and performs it inside `failure()`. It preserves public generation types, response schemas, parsing rules, HTTP status/details and error classifications. It does not replace the SDK, persist the Provider, change a transaction, increase admission or remove a safety guard.

In the small sequential A/B, unprofiled Provider mean improved **1462.73 → 1351.63 ms (−7.60%)**, and Chat server mean **1619.49 → 1499.05 ms (−7.44%)**. These are separate measurements from the later capacity A/B. The named import-stage total decreased **616.17 → 519.41 ms**: the protocol boundary fell sharply, but some shared dependency work moved into HTTPX/OpenAI imports. Reporting the protocol-boundary reduction alone would overstate the net saving.

Regression checks covered a standalone protocol import that explicitly forbids FastAPI/Starlette, API error translation, timeout classification, bounded IPC, no redirects/retries, atomic Job assignment and cleanup uncertainty remaining fail-closed. The same subprocess-per-call, memory guard and process-tree cleanup implementation remained in place. See [optimization verification](BENCHMARK_PROVIDER_OPTIMIZATION.md).

## 7. Controlled final A/B results

The final comparison used **A1 → B1 → B2 → A2 → A3 → B3**, three fresh repetitions per version, at S1 / 1.5 scheduled req/s / four outstanding / 120 seconds. Each repetition had 180 submitted requests and the same accepted query mix. Both versions used the same snapshot-loading adapter; only the Provider protocol snapshot differed. No internal profiling markers were enabled for this capacity comparison.

| Outcome per repetition | Original A | Optimized B |
|---|---:|---:|
| Scheduled / sent / completed HTTP | 180 / 180 / 180 | 180 / 180 / 180 |
| Admitted / total successful | 120 / 120 | 120 / 120 |
| In-window / drain successful | 119 / 1 | 119 / 1 |
| Successful throughput | 0.991667 req/s | 0.991667 req/s |
| HTTP 429 | 60 (33.333%) | 60 (33.333%) |
| 503 / timeout / transport / client not-sent | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |

| Successful-call metric, equal-sized three-run mean | Original A | Optimized B | Change |
|---|---:|---:|---:|
| Provider wall time | 1623.98 ms | 1492.16 ms | −8.12% |
| Chat server time | 1766.80 ms | 1635.38 ms | −7.44% |
| Provider Job CPU per call | 1.25994 s | 1.13016 s | −10.30% |

Every paired comparison favored B for Provider latency (−6.69% to −10.17%), successful server latency (−6.11% to −9.41%) and Provider CPU per call (−8.91% to −11.97%). Every throughput/rejection delta was exactly zero. These are descriptive paired observations, not confidence intervals or results obtained by comparing against an older run.

All six runs had the exact scheduled-index pattern **200, 200, 429**. At 0.667-second arrival spacing, the third arrival occurs about 1.333 seconds after the first while the two earlier requests remain in flight. Both versions still reject it; the next arrival is around 2 seconds. Lower execution latency did not cross this acceptance boundary. The interpretation is supported by admission decisions, request timelines and source ownership; exact admission-slot occupancy is not a separate timer.

Provider sampled RSS was also lower, but process timing affects sampled concurrent memory, so it is not an exact per-call peak claim. API/Worker resource observations did not establish a consistent material change. Client clock resolution was 15.625 ms; all successful completions were at least 156 ms from the cutoff, avoiding ambiguous window/drain classification. Each repetition had only 120 successful latency observations, so no per-run p95/p99 was reported.

**Result: latency and CPU improvement without demonstrated capacity improvement at this fixed offered load.** Details, resources and per-repetition results are in [the final capacity A/B report](BENCHMARK_CAPACITY_AB_REPORT.md).

## 8. Correctness and reliability

The final A/B checked all **1,080 measured submissions**, rather than sampling database outcomes:

- 720 successful requests each had exactly one correct ChatTurn, a new user/assistant Message pair and matching evidence; question/answer content, response hashes and citations matched the oracle.
- 360 rejected requests left no ChatTurn, new Message or evidence. Rejected/unused sessions and initial messages remained unchanged. Ownership/bindings and stable non-Chat tables were verified.
- Each success mapped to one Provider metric, one valid Mock call and one owned Provider Job; rejected requests had none. No duplicate execution/persistence was observed in this unique-request workload. It does not independently re-prove every Singleflight/failure scenario.
- HTTP telemetry matched 1,080/1,080 submitted attempts. Writer drops/failures were zero; stage union and unexplained-time accounting reconciled. Worker remained READY without restart during measurement.
- Every repetition ended with API exit 0, Worker STOPPED, recorded process handles exited, Job/storage lease released and the owned PostgreSQL container removed. Independent cleanup checks confirmed all six containers absent.

The earlier experiments retain their own correctness and cleanup evidence; overlapping regression or historical experiment counts are not combined into a project-wide score. Prior Phase 4 crash/recovery acceptance remains a separate source of reliability evidence, not an incident exercised by these normal-load runs.

## 9. Limitations, reproducibility and future work

The verified scope is one Windows API process and the existing Windows Job/Supervisor architecture. The fixed D1 knowledge base is small; a local 200 ms Mock does not model remote latency, streaming, token-generation variability, rate limits or production failure rates. Model/OS/filesystem caches were warmed, not controlled cold. Short runs and limited repetitions constrain percentile and statistical claims. The admission policy was deliberately preserved; maximum sustainable capacity remains unknown.

Three possible future investigations remain **unexecuted**: test another explicitly approved arrival pattern to locate an acceptance boundary; expand dataset/context size while preserving correctness controls; or evaluate a separately authorized real-provider workload with cost and privacy safeguards. None is a recommendation to increase admission without reliability verification.

### Evidence and source lineage

All measured benchmark runs used HEAD `8b1b0d9b605d25e7e6d6101bf21a60708aa33135` plus recorded uncommitted tool/source snapshots. The final local commits organize those accepted files; **the benchmarks were not rerun against newly created commit IDs**. Historical reports retain their original Git-state descriptions.

| Accepted report | Preserved local evidence root under `.local/` |
|---|---|
| [Baseline reference](BENCHMARK_BASELINE.md) | `benchmark-baseline-20261023/` (preparation, not a load result) |
| [Workload design](BENCHMARK_WORKLOAD_DESIGN.md) | Design parameters; actual manifests in subsequent runs |
| [C=1](BENCHMARK_C1_BASELINE_REPORT.md) | `benchmark-c1-complete-20261023-01/` |
| [C=2](BENCHMARK_C2_REPORT.md) | `benchmark-c2-20261023-01/` |
| [S0/S1](BENCHMARK_S0_S1_COMPARISON.md) | `benchmark-arrival-s0-s1-20261023-01/` |
| [Historical S1 1.5 req/s](BENCHMARK_S1_1_5RPS_REPORT.md) | `benchmark-s1-15rps-20261023-01/` |
| [Profiling](BENCHMARK_PROVIDER_PROFILING.md) | `provider-profile-20261024-02/` |
| [Sequential optimization A/B](BENCHMARK_PROVIDER_OPTIMIZATION.md) | `provider-optimization-20261024-01/` |
| [Capacity A/B](BENCHMARK_CAPACITY_AB_REPORT.md) | `capacity-ab-20261024-01/` |

Raw ledgers, local model caches, snapshots, one-off coordinators and analysis outputs remain local and deliberately uncommitted. A fresh clone contains the reviewed reusable tools/tests and reports, **not** these raw results or offline model files. Evidence paths are provenance references, not claims that those artifacts are published in Git. Earlier failed-run evidence and pre-existing untracked Phase 3/4 evidence were preserved.

For a future authorized reproduction:

1. Use the verified Windows/Python dependencies, pinned PostgreSQL image and the matching offline model cache expected by `rag_c1_preflight.py`; verify model-file hashes before use. Supply isolated synthetic configuration, not a development `.env` or real Provider key.
2. Generate D1 with `rag_c1_dataset.py` and create a **fresh** real preflight gate using `rag_c1_preflight.py --output <new-gate>` without `--measure-gate`. No prior local gate is shipped in Git.
3. Freeze actual Git SHA/diff, tool hashes, environment, model/vector manifest, settings, request schedule and safety limits. For A/B, recover the original protocol from `a577a18e10f0cf5049c2ab47b90710362f095567` and the optimized protocol from `26c6d13deb34f31c6b9a6a9f2f271c3c6668ffe0`; create new snapshots/freeze metadata required by `capacity_ab_run.py`. Do not patch the live checkout between repetitions.
4. Use the appropriate reviewed entrypoint and a new directory for each authorized repetition. Its own CLI and supporting report define the schedule and gate requirements. Never rerun into accepted evidence directories.
5. Retain requests, outcomes, manifests, telemetry, post-run database checks and cleanup evidence. Report not-sent attempts, failures, drain and missing observations explicitly. The historical `.local` analysis scripts are retained locally; reproduction elsewhere must reconstruct the documented definitions rather than assume those scripts were committed.

Historical SHA256 values describe recorded local bytes. Git line-ending normalization can change file-byte hashes across checkouts; Git blob identity plus a newly frozen run manifest is the portable reference.

## 10. Engineering lessons

1. A slow parent stage identifies where to investigate, not its internal cause. Separating bootstrap, SDK, HTTP and cleanup avoided blaming the Mock or database without evidence.
2. An import can look cheap in a long-lived API and become expensive when repeated in every fresh child. However, removing one eager import can relocate shared initialization; total request measurements are essential.
3. Faster rejected requests distort aggregate latency and closed-loop offered load. The C=2 burst made rejection accounting and controlled arrivals necessary.
4. Admission decisions are discrete. A real latency improvement may not change which periodic arrivals can enter, so performance and capacity claims must be separated.
5. Correctness, failure classification, identity and verified cleanup are prerequisites for trustworthy performance evidence. Optimizing by weakening process containment would answer a different question.

## Finalization and verification

| Local commit | Purpose |
|---|---|
| `a577a18e10f0cf5049c2ab47b90710362f095567` | Reviewed RAG workload, arrival accounting, Provider profiling/A/B tools and benchmark correctness tests (27 files) |
| `26c6d13deb34f31c6b9a6a9f2f271c3c6668ffe0` | Deferred FastAPI import and bootstrap/error-translation regression tests (2 files) |
| Documentation commit containing this report | Final report and eight historical supporting reports; resolve exact SHA with `git log -1 --format=%H -- BENCHMARK_FINAL_REPORT.md` |

Final focused verification used the accepted implementation, with normal Windows temporary-directory and loopback access:

| Check | Result |
|---|---|
| Benchmark tooling: `test_rag_*.py` | 24 passed |
| Provider: `test_provider*.py` | 15 passed |
| Generation: `test_generation_reliability.py` | 33 passed |
| Chat correctness: `test_chat*.py` | 24 passed |
| Full-backend Ruff | Passed |
| Working/staged diff whitespace checks | Passed |
| Staged content/path review | Explicit allowlists; no new `.local`, raw evidence, `.env`, databases or private credentials |

From `backend/`, the test commands are `python -B -m unittest discover -s tests -p '<pattern>' -v` using the existing venv. From the repository root, use `backend/.venv/Scripts/ruff.exe check backend` and `git diff --check`. These are focused tests, not a new performance programme or a claim that all backend/remote CI tests were rerun. Synthetic test credentials are intentionally non-production; path/signature review is not a comprehensive security audit.

Finalization logs, staged reviews and reconciliation are preserved in `.local/benchmark-finalization-20261024-01/`. Reviewed changes are organized into local commits; retained untracked historical evidence means Git status is not globally empty. No push, deployment, dependency change, new optimization or admission tuning was performed.

## Career presentation

### Resume bullets

- Built a reproducible Windows RAG-serving benchmark using real FastAPI TCP, PostgreSQL and offline embeddings; tested controlled concurrent arrivals with per-request database/oracle checks, rejection accounting and verified process cleanup.
- Profiled isolated LLM Provider startup and deferred an unnecessary FastAPI import, reducing mean Provider latency **8.12%**, Chat server latency **7.44%** and Provider CPU per call **10.30%** in a controlled local-Mock A/B; documented unchanged throughput under the two-slot admission policy.

### Interview discussion

“The initial slow path was Generation, but that did not tell us whether the cost was HTTP, SDK work or process isolation. I profiled the real child and Windows Job lifecycle, checked overlapping intervals and compared profiled calls with controls. Repeated dependency initialization dominated, and the protocol imported FastAPI even though the child only needed generation contracts. I deferred that import to the HTTP error translator, preserving the fresh subprocess and cleanup guarantees.

“The final interleaved experiment showed lower latency and CPU, but both versions still accepted two of every three periodic arrivals. The faster execution had not crossed the next admission boundary, so I reported no demonstrated capacity improvement at that rate. Remaining trade-offs include per-call startup overhead versus isolation, bounded admission versus utilization, and how much a small deterministic Mock workload can tell us about production. I would change one of those factors only in a separately designed and approved experiment.”
