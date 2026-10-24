# Tests and engineering evidence

## What belongs in Git

- `backend/tests/test_*.py` and `frontend/tests/`: regression tests, including security, transactions and failure boundaries.
- `backend/tests/experiments/*.py` / `*.sh`: reusable controlled experiment harnesses and helpers. Some are imported by focused tests; do not replace executable regression coverage with prose.
- `backend/docs/`: maintained operating procedures, workload contracts and curated engineering summaries.

## What remains local

- `docs/archive/benchmark/`: detailed Benchmark reports and baseline manifest.
- `docs/archive/phase4/`: full Phase 4 acceptance reports.
- `docs/archive/phase3/`: historical experiment `_REPORT.txt` files formerly in the test tree.
- `backend/tests/experiments/evidence/`: raw run output, JSON results and cleanup records, kept at original paths and ignored by Git.
- `.local/`: isolated experiment resources, logs, manifests and original evidence.

Historical reports retain their original contents and path references; interpret those paths relative to the original repository layout. Public summaries distinguish observations from hypotheses and production guarantees. Raw evidence is not available in a fresh clone. Removing files from current tracking does not erase earlier Git history or change GitHub until a commit is pushed.

## Validation commands

From the repository root, focused benchmark tools and Provider regression checks:

```powershell
Push-Location backend
.venv/Scripts/python.exe -B -m unittest discover -s tests -p 'test_rag_*.py' -v
.venv/Scripts/python.exe -B -m unittest discover -s tests -p 'test_provider*.py' -v
.venv/Scripts/python.exe -m ruff check .
Pop-Location
git diff --check
```

These unit checks do not execute a capacity benchmark. For full regression commands and isolation requirements see [development](development.md), the [backend CI workflow](../../.github/workflows/backend-ci.yml) and [frontend CI workflow](../../.github/workflows/frontend-ci.yml). PostgreSQL-specific skips are not passes; never substitute the normal development database for an isolated test target. Experiment scripts must be selected deliberately, not run as a batch against user data.

For deployment/recovery use [Windows deployment](windows-deployment.md), [backup/restore](backup-restore.md), [release/rollback](release-rollback.md) and [incident response](incident-response.md).
