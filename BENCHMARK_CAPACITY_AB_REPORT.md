# Provider optimization: capacity A/B verification

2026-10-24 — **NO CAPACITY BENEFIT at the tested offered load.** Experiment execution, correctness and cleanup passed in all six repetitions. The optimized Provider retained latency and CPU savings, but did not increase successful throughput or reduce rejection at 1.5 req/s.

## 1. Frozen identity and scope

- Local branch `main`, HEAD `8b1b0d9b605d25e7e6d6101bf21a60708aa33135`. The pre-existing, uncommitted Step 7 `protocol.py` import optimization remains the only tracked production change. This step made no production changes, commits or pushes.
- **A:** original protocol with module-level FastAPI import; **B:** accepted function-local import inside `failure()`. Original snapshot SHA256 `7f6d9d5af32e213f1aea0d05f843ff784cc4798485d0e7f192a6c153c9e7cec6`; optimized `3a5afe7324cbfcb8338635117857844bca8983165c980f1121693612a6e411ae`.
- Reused Step 7's identical frozen-protocol loading shim in real Provider subprocesses. All A generated entrypoints have one SHA; all B entrypoints have one SHA. Their generated source differs only in snapshot path; the snapshot code differs only by the accepted import optimization. The API parent uses the optimized protocol in both arms, where FastAPI is already loaded. This tests the child bootstrap difference rather than two separate full application checkouts.
- Windows 11 build 26300; i9-13980HX, 24 physical cores / 32 logical processors; Python 3.12.10; FastAPI 0.136.0, OpenAI SDK 2.32.0, HTTPX 0.28.1, FastEmbed 0.9.0. The complete installed dependency set, model files and PostgreSQL configuration matched the frozen environment in every run. Shared development host, no exclusive CPU allocation.
- Real single-process FastAPI TCP, isolated PostgreSQL **18.6**, offline FastEmbed and Persistent Query Worker, actual Provider subprocess/Windows Job isolation, local deterministic Mock with unchanged `Event.wait(.2)` minimum wait. No real paid Provider calls.
- D1 SHA256 `178365eac1181925ffaed23b2a919c3b5f99f2c8a385f58ed5823216fdc9aa77`: 20 documents / 200 real indexed chunks / 384-dimensional vectors; model `fastembed:sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`. Every repetition reproduced vector/content identity and passed all 60 preflight queries.
- Independent sessions, S1 enabled, **1.5 scheduled req/s, four client slots, 120 seconds, 180 planned arrivals**, no retries/queued catch-up. Same two-slot Chat admission, Provider/Worker timeouts and recovery policies. Each repetition recreated PostgreSQL, storage, API and Worker, then ran the same 60-query warmup. Measurements exclude preparation/warmup and offline verification; drain is reported separately.

New evidence root: [`.local/capacity-ab-20261024-01`](.local/capacity-ab-20261024-01/). `freeze.json` freezes 94 source/tool files and exact order **A1 → B1 → B2 → A2 → A3 → B3** before execution. `pattern-and-source.json` verifies the generated entrypoints and accepted-query mix. `final-integrity.json` reconciles source, configuration, manifests, every arrival and unique identities. Previous evidence remains intact.

Step 8 adds only `backend/tests/experiments/capacity_ab_run.py`, `backend/tests/test_rag_capacity_ab.py`, this report and ignored local experiment artifacts. The adapter reuses the unchanged `rag_s1_15_measure.py` and scheduler; it adds ownership/message checks outside the measurement window. No internal profiling markers were enabled for these capacity runs.

## 2. Capacity results

`Completed` means all HTTP responses, including drain. `Window success` requires HTTP completion by the original 120-second boundary and subsequent oracle/database verification. Throughput denominator is always 120 seconds. Rejection denominator is sent requests, also 180 scheduled here.

| Run | Scheduled / sent | Admitted | Completed | Total / window success | Success req/s | 429 | 503 / timeout / transport | Not sent | Drain success |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A1 | 180 / 180 | 120 | 180 | 120 / 119 | 0.991667 | 60 | 0 / 0 / 0 | 0 | 1 |
| B1 | 180 / 180 | 120 | 180 | 120 / 119 | 0.991667 | 60 | 0 / 0 / 0 | 0 | 1 |
| B2 | 180 / 180 | 120 | 180 | 120 / 119 | 0.991667 | 60 | 0 / 0 / 0 | 0 | 1 |
| A2 | 180 / 180 | 120 | 180 | 120 / 119 | 0.991667 | 60 | 0 / 0 / 0 | 0 | 1 |
| A3 | 180 / 180 | 120 | 180 | 120 / 119 | 0.991667 | 60 | 0 / 0 / 0 | 0 | 1 |
| B3 | 180 / 180 | 120 | 180 | 120 / 119 | 0.991667 | 60 | 0 / 0 / 0 | 0 | 1 |

Every version/repetition had **33.333% HTTP 429**, zero client not-sent attempts and one successful drain completion. There were no other failures. Each arm had 357 in-window successes over three 120-second windows, 360 total successes and 180 rejections from 540 submissions. Mean successful throughput was **0.991667 req/s in both arms**; paired throughput and rejection-count deltas were exactly zero.

## 3. Latency and stage boundaries

Milliseconds, **mean / p50**. Each success/Provider cell has n=120; all-HTTP n=180; rejected n=60. Approved thresholds are p50≥20, p95≥200, p99≥1000. **No per-repetition p95/p99 is justified**, and runs were not pooled to manufacture those percentiles. Successful distributions include the one drain success; failed/rejected attempts are shown separately and remain in all-HTTP latency.

| Run | Success client | Success server | Generation | Provider (nested) | All client | 429 client | 429 server |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A1 | 1753.73 / 1750.00 | 1752.76 / 1749.58 | 1614.30 / 1610.72 | 1610.25 / 1606.68 | 1172.96 / 1734.00 | 11.42 / 16.00 | 9.47 / 9.43 |
| B1 | 1635.51 / 1625.00 | 1634.59 / 1631.30 | 1494.90 / 1490.30 | 1490.81 / 1486.39 | 1094.16 / 1610.00 | 11.47 / 15.00 | 9.54 / 9.50 |
| B2 | 1641.31 / 1640.00 | 1639.96 / 1634.86 | 1500.39 / 1494.53 | 1497.05 / 1491.23 | 1098.14 / 1610.00 | 11.82 / 15.00 | 9.52 / 9.57 |
| A2 | 1747.39 / 1750.00 | 1746.65 / 1743.87 | 1608.51 / 1606.16 | 1604.46 / 1602.02 | 1167.87 / 1734.00 | 8.82 / 15.00 | 9.59 / 9.44 |
| A3 | 1803.25 / 1797.00 | 1800.98 / 1794.31 | 1661.65 / 1658.06 | 1657.23 / 1653.90 | 1206.14 / 1766.00 | 11.93 / 15.00 | 9.99 / 9.93 |
| B3 | 1633.18 / 1625.00 | 1631.59 / 1628.90 | 1491.98 / 1489.09 | 1488.63 / 1485.89 | 1092.55 / 1609.00 | 11.30 / 15.00 | 9.43 / 9.26 |

Other successful-stage **means**, milliseconds:

| Run | Coordination/preparation | Routing | Material acquisition | Persistence | Cleanup | Unexplained |
| --- | --- | --- | --- | --- | --- | --- |
| A1 | 14.574 | 0.002 | 98.467 | 16.909 | 0.028 | 8.485 |
| B1 | 14.314 | 0.003 | 97.716 | 18.907 | 0.033 | 8.718 |
| B2 | 15.316 | 0.002 | 98.282 | 17.529 | 0.028 | 8.416 |
| A2 | 14.771 | 0.002 | 97.624 | 17.260 | 0.029 | 8.458 |
| A3 | 14.082 | 0.002 | 98.863 | 17.784 | 0.028 | 8.567 |
| B3 | 14.759 | 0.002 | 96.842 | 19.401 | 0.029 | 8.581 |

Admission is inside `coordination_preparation`, not a separately timed interval (`backend/app/api/routes/chat.py:125–131`). Its rejection-path stage mean was 1.33–1.46 ms; full rejected server time was about 9.4–10.0 ms. The two-slot check remains `backend/app/services/chat/capacity.py:20`. All 360 rejected requests were identified at **Chat admission**, with no Provider call; no rejection moved to Embedding Worker or a verified Uvicorn boundary.

Provider durations are contained within Generation and are **not added** to stage coverage. Offline interval-union checks matched `stage_covered_ms`, and covered plus unexplained matched server elapsed for every request. No first-level interval overlap was found within rounding tolerance. Unexplained time is not assigned to a component.

Paired B minus A comparisons (pair numbers, not execution order):

| Pair | Throughput delta req/s | 429-count delta | Provider mean delta ms / % | Success server mean delta ms / % | Provider CPU/call change |
| --- | --- | --- | --- | --- | --- |
| 1 | 0.000000 | 0 | -119.45 / -7.42% | -118.18 / -6.74% | -9.97% |
| 2 | 0.000000 | 0 | -107.41 / -6.69% | -106.69 / -6.11% | -8.91% |
| 3 | 0.000000 | 0 | -168.61 / -10.17% | -169.39 / -9.41% | -11.97% |

Across equal-sized repetitions, Provider means were **1623.98 → 1492.16 ms (-8.12%)**; successful server means **1766.80 → 1635.38 ms (-7.44%)**. These are descriptive averages of this experiment, not confidence intervals.

## 4. Resources and telemetry

CPU cores = CPU-seconds delta / observed sample elapsed time for API/Worker. Provider CPU uses whole Windows Job accounting for all 120 measured successful calls, including drain, rather than summing short-lived PID rates. RSS values below are **observed mean–maximum MiB** across in-window samples; process-group sums may double-count shared pages and are not exclusive physical memory or exact peaks.

| Run | API CPU cores | Worker CPU cores | Provider CPU total s / per call s | API RSS | Worker RSS | Provider group RSS |
| --- | --- | --- | --- | --- | --- | --- |
| A1 | 0.095 | 0.533 | 150.203 / 1.252 | 125.65–130.35 | 624.91–624.93 | 105.25–165.50 |
| B1 | 0.091 | 0.532 | 135.234 / 1.127 | 125.57–129.84 | 625.23–625.29 | 93.16–152.71 |
| B2 | 0.090 | 0.529 | 135.797 / 1.132 | 124.44–130.46 | 624.99–625.03 | 94.73–153.25 |
| A2 | 0.091 | 0.530 | 149.078 / 1.242 | 125.79–130.83 | 624.94–624.95 | 105.31–166.54 |
| A3 | 0.089 | 0.536 | 154.297 / 1.286 | 126.29–130.68 | 625.25–625.27 | 109.03–167.11 |
| B3 | 0.096 | 0.534 | 135.828 / 1.132 | 125.16–129.11 | 624.97–624.98 | 91.35–153.84 |

| Run | PG CPU% sample mean | PG samples | PG observed memory MiB | Process-sample errors | HTTP + lifecycle dropped / failed |
| --- | --- | --- | --- | --- | --- |
| A1 | 1.556 | 19 | 57.68–59.63 | 0 | 0 / 0 |
| B1 | 1.498 | 19 | 58.02–59.07 | 0 | 0 / 0 |
| B2 | 1.506 | 19 | 55.12–56.10 | 0 | 0 / 0 |
| A2 | 1.579 | 19 | 57.64–59.10 | 0 | 0 / 0 |
| A3 | 1.566 | 19 | 57.52–58.82 | 0 | 0 / 0 |
| B3 | 1.599 | 19 | 54.98–56.30 | 0 | 0 / 0 |

Each run had 119 in-window API/Worker/Provider sample rows, zero missing groups and zero sample errors; PG had 19 observations. Missing samples are not imputed as zero. PostgreSQL percentages are Docker observations, not a time-weighted CPU integral. Raw memory/private-byte/CPU observations are preserved in `api-after.json` and `external-resources.json`; generator/Mock observations are also in `analysis.json`.

Provider CPU per call decreased in every pair. Provider sampled memory was lower, consistent with the bootstrap change, but changed process timing also affects concurrent sampled RSS; this does not establish exact per-call peak savings. API/Worker CPU and memory did not show a consistent material change. PG memory differences across fresh instances are not attributed to the Provider optimization. Discrete sampling cannot rule out short spikes or contention.

HTTP telemetry was complete: 180/180 per repetition, 1,080/1,080 total. HTTP writer accepted/written rose by 180 each per run; dropped/failed stayed zero. Worker lifecycle dropped/failed stayed zero, with no new recovery event during measurement. Request, HTTP and Provider identities matched exactly. Existing counter semantics were retained.

## 5. Correctness, identity and cleanup

- Every submitted request was checked against PostgreSQL. Each of 720 successes had exactly one correct ChatTurn, one new user/assistant Message pair and matching evidence; oracle, response hash, persisted answer/question and citation/source checks passed.
- Each of 360 rejections had no ChatTurn, added Message or evidence. Rejected/unused sessions were unchanged, including their timestamps; initial messages were unchanged. Successful sessions retained owner/title/creation identity, while expected update timestamps could change.
- All sessions/workspaces/ChatTurns had the expected owner and binding. Stable non-Chat table hashes matched. There were no duplicate request/session identities across measured attempts or unauthorized state changes. This is integrity verification of the fixed authorized workload, not a new adversarial authorization test.
- Per run: 180 total ChatTurns / 1,080 Messages / 180 evidence records including 60 warmup calls. Exactly 120 measured Provider calls and Jobs per run; 720 measured, 1,080 including warmup across six runs. Rejected requests had none. Every measured Job had three total OS processes (launchers included), zero active at close.
- Worker stayed READY with zero restarts during measurement. Each API exited 0; Worker stopped; recorded process handles confirmed exit; Job and storage lease were released. All six owned PostgreSQL containers were removed and independently checked absent. No unrelated containers or historical evidence were removed.

Per-run evidence: `result.json`, `ownership-integrity.json`, `verified-requests.json`, `mock-calls.json`, `measured-telemetry.json`, `api-before.json`, `api-after.json`, `api-cleanup.json`, `postgres.json`, `source.json`, `provider-source/identity.json`. Suite checks: `suite-result.json`, `final-integrity.json`, `independent-cleanup.json`, `pattern-and-source.json`.

## 6. Interpretation and limitations

**Observed:** every run followed the exact repeating scheduled-index pattern **200, 200, 429**. Accepted query composition was identical across arms; Mock answer hashes were stable. The client used at most three of four slots, sent all 180 arrivals, and did not queue or drop work. All rejections were at the unchanged two-slot Chat admission boundary.

**Explanation supported by source and timeline:** arrivals are 0.667 seconds apart. The third arrival is about 1.333 seconds after the first, while both earlier requests still occupy execution slots; both versions still reject it. The next arrival occurs at about 2 seconds, by which time slots are available. The optimization shortened latency but did not cross the acceptance timing boundary. Thus two admissions per three arrivals remained unchanged. Exact slot-occupancy duration is not independently timed; the admission decision, in-flight HTTP records and source ownership boundaries support this interpretation.

Client clock resolution is 15.625 ms. Small negative dispatch lags (down to −10.667 ms) are within one tick; maximum lag was 41.667 ms, below the unchanged 100 ms guard. Every successful completion was at least 156 ms from the cutoff, so drain/window classification is not ambiguous at that resolution. `timeline.json` retains per-run checks. Server/Provider timing uses the finer monotonic performance counter.

**Conclusion:** latency improvement without demonstrated capacity improvement at this one periodic arrival rate. This does not reject benefits at other load patterns, prove a general capacity ceiling, justify changing admission, or establish production/paid-Provider performance. Three paired runs on a shared host are not a formal causal estimate; interleaving, frozen dependencies, identical accepted-query mix and consistent paired direction support the narrower Provider-latency result. Historical 0.992 req/s is context only; the capacity conclusion uses the contemporary A/B runs.

## 7. Reproduction, tests and final state

The six-run suite took 1,693.797 seconds (~28.2 minutes), including preparation, warmup, measurement, verification and cleanup; only six separate 120-second windows contribute throughput. Per-repetition 1,500-second and resource guards were unchanged; the suite had a predeclared 2,400-second aggregate ceiling. The existing overload rule requires ≥20 offered requests in each of two 10-second bins; at 1.5/s there are only 15/bin, so that sample gate cannot trigger. It was not weakened; other safety guards remained active.

Before execution: **24 benchmark-tool tests passed**, including two new adapter tests; **15 Provider regressions passed**. A later restricted adapter rerun hit a temporary-directory permission error; the bounded normal-access rerun passed both tests. Those two are a subset, not two additional tests. No skipped test is counted as passed. Full-backend Ruff and `git diff --check` passed before and after execution. Logs are retained in the evidence root.

Actual suite command from the repository root:

```powershell
backend/.venv/Scripts/python.exe -B .local/capacity-ab-20261024-01/run.py
```

Do not rerun into these existing directories. A future authorized reproduction needs a fresh evidence directory, the same frozen snapshots/order/configuration and a new source/tool manifest. Single-repetition entrypoint is `capacity_ab_run.py --output <fresh A1/B1/etc directory> --measure-gate .local/benchmark-c1-complete-20261023-01/preflight`; its parent must contain the matching freeze and snapshots.

Offline verification (does not launch a workload):

```powershell
backend/.venv/Scripts/python.exe -B .local/capacity-ab-20261024-01/analyze.py
backend/.venv/Scripts/python.exe -B .local/capacity-ab-20261024-01/integrity.py
backend/.venv/Scripts/python.exe -B .local/capacity-ab-20261024-01/timeline.py
backend/.venv/Scripts/python.exe -B .local/capacity-ab-20261024-01/compare.py
backend/.venv/Scripts/ruff.exe check backend
git diff --check
```

Test commands from `backend/`:

```powershell
.venv/Scripts/python.exe -B -m unittest discover -s tests -p 'test_rag_*.py' -v
.venv/Scripts/python.exe -B -m unittest discover -s tests -p 'test_provider*.py'
```

Final Git status is retained in `git-after.txt`: accepted Step 7 production change remains uncommitted; this step adds benchmark-only tooling/tests/report. No further production optimization, admission change, load level, paid call, commit or push occurred. **Stop here: NO CAPACITY BENEFIT, with successful experimental and correctness verification.**
