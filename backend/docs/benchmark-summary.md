# Backend benchmark: engineering summary

Historical measurements finalized on 24 October 2026; repository organization does not rerun them.

## Method and scope

Windows 11, i9-13980HX, approximately 31.62 GiB RAM, Python 3.12.10; one FastAPI process, real TCP HTTP, isolated PostgreSQL 18.6, offline FastEmbed Persistent Query Worker and real Windows Job-isolated Provider subprocesses. The LLM endpoint was a local deterministic Mock with a 200 ms minimum wait, not a paid model.

D1 consists of 20 synthetic documents, 200 indexed chunks and 60 fixed queries with evidence oracles. Repetitions use fresh database/storage/API/Worker lifecycles and 60 warmup requests. Measurement excludes preparation and offline verification. Requests have distinct sessions and IDs, bounded outstanding concurrency and no retries. Goodput counts successful responses completed inside the window; drain completions remain separate. See [workload design](benchmark-workload-design.md) and the reusable tools in [experiments](../tests/experiments/).

## Findings

| Experiment | Observation and limit |
| --- | --- |
| C=1, default S0 | Three runs: 0.5667 / 0.5667 / 0.6083 successful req/s, no rejection. |
| Closed-loop C=2, S0 | Each run reached the 300-request cap in about 3.3–3.5 s: 2 successes and 298 HTTP 429. A rejection storm, not a steady-state capacity estimate. |
| Fixed 1 req/s, maximum 2 outstanding | S0 mean goodput 0.5000; S1 mean 0.9889. First S1 repetition had one client not-sent attempt. |
| S1, 1.5 req/s, maximum 4 outstanding | 0.9917 successful req/s, 33.33% HTTP 429. Outstanding limit differs from the 1 req/s study. |
| Final original/optimized A/B | Same goodput and rejection rate; lower Provider latency, Chat latency and CPU. |

Profiling identified substantial subprocess bootstrap/import cost. The only production optimization moved the FastAPI import in `llm/protocol.py` into the HTTP error translator. Job Object isolation, IPC, timeouts, cleanup and admission policies were preserved.

Final A/B order was A1 → B1 → B2 → A2 → A3 → B3, three repetitions per version, 120 s each, S1, 1.5 req/s, maximum four outstanding requests. Provider mean decreased 1623.98 → 1492.16 ms (8.12%), successful Chat server mean 1766.80 → 1635.38 ms (7.44%), and Provider CPU/call 10.30%. Each repetition had 180 sent, 120 successful (119 in-window, one drain), 60 HTTP 429, and zero other failures/client drops. Periodic arrivals and the two-slot admission policy produced identical acceptance patterns. This is latency improvement without demonstrated capacity improvement.

## Correctness and evidence

All 1,080 submitted measured requests across final A/B were checked: 720 successes and 360 rejections. Checks cover persistence, ownership, evidence oracle, Provider attribution, duplicate/partial writes, telemetry and process/database cleanup. Provider intervals are nested in Generation and are never added again to stage coverage. No success p95 is reported for 120 samples; thresholds are p50 ≥20, p95 ≥200 and p99 ≥1000.

Historical finalization checks: 24 benchmark-tool, 15 Provider, 33 Generation and 24 Chat tests passed in separate suites, plus Ruff and diff checks. These are saved historical results, not a claim that this documentation change reran the entire backend.

## Reproducibility and limitations

Benchmark source base was `8b1b0d9b605d25e7e6d6101bf21a60708aa33135` plus frozen, uncommitted tool/application snapshots. Subsequent commits preserve tools (`a577a18e10f0cf5049c2ab47b90710362f095567`), optimization (`26c6d13deb34f31c6b9a6a9f2f271c3c6668ffe0`) and reports (`f919f29489b19059fef94d6252950d0ebb4bfff0`). Measurements were not rerun at those commits.

Full reports are retained locally under `docs/archive/benchmark/`; raw runs remain in `.local/` and `backend/tests/experiments/evidence/`. They are intentionally excluded from new repository snapshots. Historical committed evidence remains recoverable in Git history; this organization does not rewrite history. A fresh clone has tools and methodology, not the author's raw data, cached models or local coordinators. Provision isolated resources, establish a fresh preflight gate and source/configuration hashes before any new run.

This shared-host synthetic benchmark does not establish maximum sustainable capacity, paid-LLM performance, Linux compatibility, multi-process safety, remote CI execution or production readiness at scale.
