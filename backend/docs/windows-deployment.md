# Windows deployment baseline

Scope: one Windows FastAPI process, the existing Windows Job Object Provider and
Persistent Query Worker Supervisor, PostgreSQL, and Next.js. No Linux backend,
remote Worker, multi-process API, automatic database replacement, or new migration.

## Environment

Use Python 3.12, Node.js 22.13+, PowerShell, Docker Desktop with Linux containers
for PostgreSQL only, and the pinned backend requirements / frontend package lock.
The isolated verification uses PostgreSQL 18.6 (`postgres:18-alpine`). Record the
image digest for a deployment; the tag alone is not an immutable version pin.

For a new installation, follow README's PostgreSQL, virtual environment, `configure`,
`init`, `prepare`, `npm ci`, and `npm run build` steps. `init` is for an explicitly
selected new database, not an automatic startup migration. Reuse an existing
database/container and its volume; never create a replacement to hide a failure.

`backend/.env` is the configuration source for `app/cli/local.py`. It overrides
same-named inherited environment variables. `configure` refuses to overwrite an
existing file and generates JWT/Fernet secrets without printing them. Preserve
`ENCRYPTION_KEY` across restarts/restores or stored Provider keys cannot be decrypted.
Keep `.env`, database credentials, data, uploads, model cache and runtime logs out
of commits. Back up the database and corresponding storage/keys together.

Required deployment choices in `backend/.env`:

```dotenv
APP_PORT=18001
FRONTEND_URL=http://localhost:3001
LOCAL_DATA_DIR=../data/local-mvp
EMBEDDING_BACKEND=fastembed
EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
PERSISTENT_QUERY_WORKER_ENABLED=true
LLM_BACKEND=provider
RUNTIME_METRICS_ENABLED=true
STARTUP_RECOVERY_ENABLED=false
WORKSPACE_DELETE_MAINTENANCE_MODE=false
SESSION_CHAT_CONCURRENCY_ENABLED=false
```

Also retain the generated JWT settings, `ENCRYPTION_KEY`, and a loopback PostgreSQL
`DATABASE_URL`. Configure `LLM_ALLOWED_BASE_URLS` for the intended endpoint and enter
the per-user Provider key through Settings. Controlled verification uses only a
local Mock LLM. `--test-ai` configures simulated embeddings too, so it does not
verify the Persistent Worker. `prepare` downloads the model in advance; Worker
startup uses the local cache offline. Restart budget defaults are unchanged.

Next.js uses same-origin `/api` and rewrites to `http://127.0.0.1:18001` by default.
If changing the API port, set `ASTERIA_API_URL` before building the frontend and
rebuild. A stale `.next` build can contain a stale destination.

## Normal startup and graceful stop

1. Start Docker Desktop and the existing PostgreSQL container:
   `docker start asteria-local-mvp-db`.
2. In a dedicated PowerShell terminal at the repository root:

   ```powershell
   Push-Location backend
   .venv/Scripts/python.exe -B app/cli/local.py serve --env-file .env
   # Keep this terminal open. To stop gracefully: Ctrl+C, then wait for exit.
   Pop-Location
   ```

   The launcher binds loopback, uses `workers=1`, and does not run DDL. Do not add
   `--reload`, multiple workers or a second API launcher. Persistent Worker failure
   during initialization aborts startup. Wait for application startup completion.
3. In another terminal:

   ```powershell
   Push-Location frontend
   npm run start
   # Ctrl+C to stop this frontend.
   Pop-Location
   ```

4. Run `./Start-Asteria.ps1 -CheckOnly`. It checks API health, API readiness,
   frontend page, proxy health/readiness, and a database query without DDL or LLM calls.
   Health/readiness do not prove runtime Worker health: current `/api/ready` checks
   the recovery gate/database; Worker initialization and lifecycle evidence are
   separate. Confirm Worker initialization and a controlled Chat before acceptance.
5. Stop frontend traffic, then Ctrl+C in the API terminal. Wait for lifespan shutdown
   and process exit. Confirm `shutdown_completed` when telemetry is available and
   no owned Worker remains. Telemetry may drop records; absence is not exit proof.
   Cleanup uncertainty requires investigation; do not immediately launch a replacement.
6. Optionally `docker stop asteria-local-mvp-db` after the application exits. Keep its
   data volume. PostgreSQL need not stop on every application shutdown.

The existing `Start-Asteria.ps1` hidden-window shortcut remains available but is not
the graceful-stop baseline. `Stop-Asteria.ps1` forcibly stops recorded process trees;
it does not guarantee Uvicorn lifespan completion. Unknown listeners are preserved,
not validated as this application. Use the foreground workflow for controlled stops.

## Recovery and limits

Normal operation above does not perform startup recovery. Pending deletion blocks
readiness; do not delete the record to bypass it. Follow `workspace-recovery.txt`
under explicit single-instance maintenance, then return to non-maintenance settings
and restart normally. Maintenance intentionally rejects normal resource writes.
Do not enable maintenance just to get a green startup check.

Only one API process and one telemetry writer process are supported. The local
launcher is not a Windows Service manager. TLS, remote exposure, unattended service
installation, automated backup/restore and machine-reboot recovery are not verified
by this baseline.

## Acceptance evidence (2026-10-23)

The preserved Unit 1 report is a historical acceptance snapshot; its statement that
work was uncommitted describes that time. Local baseline commits are `1bd1f11`
(implementation/tests) and `9e0e486` (acceptance/schema documentation).

Current deployment evidence is kept locally under `.local/windows-deployment-*`.
The smoke uses real TCP through the existing built Next.js server, fresh PostgreSQL,
actual FastAPI lifespan, offline FastEmbed Worker and local Mock LLM through the
Windows Provider subprocess. The API is hosted by a single-process test harness,
not by the PowerShell shortcut; its shutdown sets Uvicorn's `should_exit`, exercising
lifespan cleanup rather than injecting an actual terminal Ctrl+C. Frontend teardown
is forced and is not evidence of graceful Next.js shutdown. No browser UI automation
or real paid Provider acceptance is claimed. Experiment scripts/data are excluded
from the baseline commits; retain them locally for reproduction.

Verification results:

- `.local/windows-deployment-smoke2-20261023/smoke-result.json`: PostgreSQL 18.6,
  proxy login/register/me, document indexing, grounded Chat (one Mock Provider call),
  Worker READY, Supervisor STOPPED, Job/slot release and three owned process exits.
  The first run was intentionally in maintenance mode and correctly rejected resource
  creation; its evidence is preserved under `windows-deployment-baseline-20261023`.
- `.local/windows-cli-smoke2-20261023/cli-result.json`: the actual `local.py serve`
  launcher with a temporary `.env` and isolated PostgreSQL reached READY; Windows
  Console Break produced `Application shutdown complete` and confirmed Worker exit.
  Exit code was 3 after the console signal, not a claimed zero exit. This separately
  verifies real CLI shutdown; it does not replace the full proxy/Chat smoke above.
  The first CLI attempt lacked telemetry in its synthetic settings, was stopped,
  and remains preserved rather than counted as a graceful-shutdown pass.
- `.local/windows-deployment-baseline-20261023/regression.log`: 350 discovered,
  342 passed, 8 PostgreSQL-specific skips, no failures, 114.088 seconds. Do not count
  the PostgreSQL smoke as execution of the skipped recovery tests. Historical Unit 1
  PostgreSQL acceptance remains separate evidence.
- `frontend-build.log`: `npm run build` passed using installed dependencies.
- `ruff.log`: `ruff check backend/app backend/tests` passed; `git diff --check` passed.

No production database was used. All disposable databases and frontend processes
were stopped; temporary CLI environment files were removed. Synthetic storage and
logs remain under `.local` as evidence. Unit 1's historical report remains unchanged.
