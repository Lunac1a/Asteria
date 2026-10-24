# Windows release and rollback gate

Status (2026-10-23): **CONDITIONAL PASS in the isolated local scope**. Next.js
16.4.1 removed the build blocker. Both rollback scenarios passed with real Windows
CLI/TCP and PostgreSQL; see PHASE4_UNIT3_SPRINT2_REPORT.md. Remote Actions execution,
a committed release candidate and real Persistent Worker release switching remain
outside this verification. Development-only braces advisory remains open.

## Before a release

1. Obtain an explicitly approved commit containing all required implementation and
   tests. Record its full SHA, dependency lock/requirements hashes, Python/Node
   versions, PostgreSQL version/image digest, and evidence paths. An uncommitted
   working tree is not an immutable release. Do not commit or push automatically.
2. Use a separate clean checkout of that SHA. Run both existing workflows at that
   exact revision, including when path filters would otherwise skip a workflow
   (`workflow_dispatch` is now available). Review discovered/pass/fail/skip counts;
   a missing job or skipped PostgreSQL case is not acceptance. Local validation does
   not establish GitHub Actions execution.
3. From backend: `python scripts/check_release_files.py`, `ruff check .`, and
   `python scripts/ci_tests.py all` on Windows. Run `python scripts/ci_tests.py postgres`
   only against an explicitly disposable `recovery_protocol` database with the exact
   `ASTERIA_RECOVERY_TEST_PGDATA` and loopback URL. This lane refuses missing settings
   and unexpected skips. It is not a full PostgreSQL concurrency benchmark.
4. From frontend: `npm ci`, `npm run lint`, `npm test`, `npm run build`. Use the intended
   `ASTERIA_API_URL` at build time. A nonzero exit or a security gate blocks release
   even if compilation completed. Do not bypass it by retrying or changing flags.
5. Review secrets separately: tracked-file exclusion is only a path check, not a
   secret scanner. Keep `.env`, credentials, uploads, dumps and keys out of the
   release; inspect templates and changed source for embedded secrets. Provision
   configuration securely in the new checkout. Preserve Fernet key and canonical
   absolute `LOCAL_DATA_DIR`; a relative path can accidentally select different data
   when the checkout moves. Do not run `configure` over an existing environment.

## Controlled application switch

Follow `windows-deployment.md` for foreground single-process startup/shutdown and
`backup-restore.md` for offline backups. Confirm a compatible verified backup exists
before stopping; after stopping all writers, create/verify the final paired backup
at the actual switch boundary. Merely stopping HTTP is not quiescence.

Stop frontend traffic, then gracefully stop the API and confirm owned Worker and
Provider processes have exited. Unknown cleanup means stop and investigate, not
launch a replacement. Keep PostgreSQL and storage intact. Do not use the force-stop
shortcut as proof of graceful cleanup. Do not run old and new APIs concurrently.

Activate the separately prepared verified checkout/environment. Do not run `init`
or arbitrary migration automatically. Use `app/cli/local.py serve --env-file .env`;
its schema and incomplete-restore checks must pass. Check `/api/health`, `/api/ready`,
the existing `Start-Asteria.ps1 -CheckOnly` including frontend proxy/DB, Worker
initialization evidence and controlled authentication/document/Mock Chat smoke.
Readiness alone does not establish runtime Worker health. Record UTC timestamp,
old/new SHA, backup manifest hash, target identity (no credentials), checks and final
ACCEPTED/REJECTED/ROLLED_BACK status. Reopen traffic only on explicit acceptance.

## A: Application failure, data still compatible

Stop the failed release safely. Before selecting the old checkout, establish that
no incompatible schema/data mutation occurred and its schema preflight still passes.
Reapply the previous recorded SHA, environment and unchanged database/storage pair;
repeat readiness, authentication, document and idempotent Chat checks. Compare
expected data invariants. If compatibility is uncertain, do not attempt code-only
rollback. Do not use `git reset --hard` in the shared working tree.

## B: Schema or persistent data incompatible

Keep all traffic/writers stopped. Code-only rollback is not safe. Use the existing
backup CLI with the matching code/dependencies and separately held encryption key.
Restore the paired database/files into independently identified empty PostgreSQL
and a new storage directory; never overwrite the live/original pair. Follow all
target identity, checksum, maintenance and pending-recovery restrictions. A failed
restore retains `.restore-incomplete`; do not remove it to force startup. Docker
timeout is not proof of cancellation. Keep the failed target offline for inspection.

Validate restored data and application smoke before switching configuration to the
new pair. Restore selects a backup point: later writes are not automatically merged
and may be lost; agree the recovery point before reopening. Preserve the failed pair
and evidence. No arbitrary schema downgrade is promised.

Both scenarios were exercised in `.local/release-resume2-20261023/`: immutable
hash-identified application copies, simulated embeddings, local Mock Provider,
real PostgreSQL and Windows CLI. This does not certify an arbitrary future commit.
Re-run the gates for each approved release SHA. For the remaining braces dev-tool
advisory, avoid untrusted glob input, keep CI isolated without production secrets,
retain bounded job times and monitor the upstream fix; do not force-downgrade the
Next.js lint configuration or hide the audit finding.


Historical evidence note (2026-10-25): phase reports referenced above are local archives under `docs/archive/phase3/` or `docs/archive/phase4/`. Raw experiment evidence is retained locally and excluded from current Git tracking. See the maintained `backend/docs/verification.md` for evidence policy; these historical references are not fresh-clone prerequisites.
