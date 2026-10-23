# Offline Windows backup and restore

Supported boundary: one stopped Windows Asteria deployment, one PostgreSQL database
in an explicitly identified local Docker container, and local regular files.
No live backup, remote database, cluster failover, automatic migration or in-place
restore is provided. Use the pinned backend environment and the same PostgreSQL
major version for source and target. Tested with PostgreSQL 18.6.

## Establish quiescence first

1. Block new traffic. Stop FastAPI gracefully and verify API, Persistent Worker,
   indexing and Provider subprocess termination. Stop CLIs, scheduled jobs,
   maintenance scripts and external database/filesystem writers too. Merely stopping
   Next.js, enabling maintenance, or pausing HTTP is insufficient.
2. Complete pending Workspace deletion recovery using `workspace-recovery.txt` in
   its existing single-instance maintenance mode. Resolve `processing` documents.
   Then stop that recovery process and verify its database sessions have ended.
   Do not rewrite immutable recovery roots/manifests or delete pending rows.
3. Prevent external writers from reconnecting until backup completes. Retain the
   same canonical storage root and filesystem permissions. `--confirm-stopped` is
   an operator attestation, NOT process discovery or an access-control switch.
4. The tool additionally takes the existing storage lease, rejects other database
   sessions, and holds SHARE locks on mapped tables throughout file copy and dump.
   These block database writes but cannot stop arbitrary filesystem writers or
   legacy programs that ignore the lease. If exclusion is uncertain, do not back up.

## Select the source explicitly

Run from `backend`. Configure `ASTERIA_BACKUP_DATABASE_URL` in this process via your
secure credential mechanism. It is a SQLAlchemy URL (`postgresql+psycopg://...`) for
the exact loopback database; the tool never defaults this to `DATABASE_URL`.
Do not paste credentials into a command line, report, manifest or Git. The backend
configuration must otherwise be available for imports; it is not copied.

Read and independently verify the container and cluster identity:

```powershell
docker inspect --format '{{.Id}}' YOUR_POSTGRES_CONTAINER
docker exec FULL_CONTAINER_ID psql -XAt -U BACKUP_ROLE -d APP_DATABASE -c "SELECT current_database(), current_setting('data_directory'), system_identifier FROM pg_control_system()"
```

Use an authorized database administrator with access to all application tables and
`pg_control_system()`. Container-local PostgreSQL authentication must already work;
the tool does not weaken it or forward credentials in argv. SQLAlchemy and the
container tools must identify the same database, data directory and cluster ID.
Do not confuse the physical PGDATA path with `LOCAL_DATA_DIR`.

```powershell
.venv/Scripts/python.exe -B -m app.cli.backup backup `
  --container FULL_CONTAINER_ID --database APP_DATABASE --user BACKUP_ROLE `
  --expected-pgdata /ACTUAL/PGDATA --expected-system-id ACTUAL_SYSTEM_ID `
  --storage D:/Asteria/data/local-mvp --bundle E:/Backups/asteria-NEW-ID `
  --confirm-stopped

.venv/Scripts/python.exe -B -m app.cli.backup verify --bundle E:/Backups/asteria-NEW-ID
```

The destination must not exist and must be outside application storage. A bundle
contains `database.dump`, `files/`, and a manifest written last. The custom-format
`pg_dump` is a consistent database snapshot; stopped writers plus the lease/locks
provide the matching file boundary. Only referenced document files and existing
staged files named by terminal recovery manifests are included. Pending operations
are refused because their absolute roots prevent safe relocation.

The manifest records PostgreSQL version, source identity/root, a deterministic
current-model DDL hash, reflected schema, and SHA-256/size for every component.
The hash is a code compatibility identifier, not an Alembic revision or a signature.
Missing manifest means incomplete backup. Verify after transfer to separate media.

Excluded: `.env`, JWT/Fernet/database credentials, model caches, logs, lock files,
temporary files and unreferenced/orphan documents. Preserve unexplained orphan files
separately before cleanup; this tool does not delete them. Model cache can be prepared
again. The dump DOES contain private document chunks, password hashes, encrypted
Provider keys, conversations and recovery metadata. Restrict backup ACLs and use
encrypted storage; checksums do not authenticate a maliciously replaced bundle.

Preserve the original `ENCRYPTION_KEY` in a separate secure secret store. Without it,
restored Provider keys cannot be decrypted. Preserve/reprovision database roles and
credentials separately; roles/ACLs/ownership are not restored. JWT signing keys may
be rotated deliberately, invalidating existing sessions. Do not restore untrusted
dumps: PostgreSQL restore executes SQL from the archive.

## Restore to a new destination only

1. Keep source and target applications stopped. Provision a separate empty application
   database (not `postgres` or a template database). Do NOT run `init` on this target.
2. Independently inspect its full container ID, database, PGDATA and system ID as
   above. Set `ASTERIA_BACKUP_DATABASE_URL` to that target. Select a new, nonexistent
   storage directory. The tool refuses the original cluster/database pair, existing
   storage, nonempty databases, target identity mismatch and PostgreSQL major mismatch.
3. Validate the trusted backup, then restore:

```powershell
.venv/Scripts/python.exe -B -m app.cli.backup restore `
  --container NEW_FULL_CONTAINER_ID --database NEW_APP_DATABASE --user RESTORE_ROLE `
  --expected-pgdata /NEW/PGDATA --expected-system-id NEW_SYSTEM_ID `
  --storage D:/Asteria/restored-NEW-ID --bundle E:/Backups/asteria-NEW-ID `
  --confirm-stopped
```

The tool verifies checksums BEFORE touching the destination. It writes an incomplete
marker, copies files, uses `pg_restore --single-transaction --exit-on-error`, and
verifies mapped columns, reflected schema and file references before removing the
marker. No `--clean`, overwrite, or automatic deletion of failed targets is used.
Text-enum constraint casts are narrowly normalized because PostgreSQL dump/restore
can represent equivalent varchar-to-text arrays differently; enum values are retained.

On error or timeout, keep the target offline. A timed-out Docker client does not prove
pg_restore stopped. Inspect the target's processes/transactions before any disposal;
do not remove `.restore-incomplete` to force startup or retry into a partial target.
Retain failure evidence and use another independently validated empty destination.
Both API startup and the supported CLI refuse an incomplete restore marker.

4. Securely supply the original Fernet key, set DATABASE_URL/LOCAL_DATA_DIR to the
   new pair, prepare the model cache, and perform documented startup checks. Terminal
   recovery records retain their historical source root and are not rewritten or
   replayed. No pending records are accepted by this workflow.
5. Verify login, workspace/document download, idempotent Chat replay and a new Mock
   Chat before reopening traffic. Startup recovery can be checked in maintenance
   mode, but normal resource writes require a separate non-maintenance startup.
6. Only after acceptance, switch the deployment configuration to the restored pair.
   Keep the source/backup and do not run both deployments as writers to one storage.

## Schema changes and rollback

There is no existing Alembic framework or schema-version table. `local.py init`
still creates missing tables only; it now wraps creation and mapped-column validation
in a PostgreSQL transaction. It cannot alter existing columns or repair arbitrary
indexes/constraints. A failure rolls back that invocation's transactional DDL.
`local.py serve` checks mapped table/column presence, type and nullability before
starting Uvicorn. Direct custom ASGI launchers do not get this full CLI check;
startup recovery retains its own checks. The restore marker blocks both paths.

Before a future schema change: stop all writers, make and verify a paired backup,
rehearse the exact change/restore in isolation, review explicit SQL and transaction
boundaries, then execute it once under maintenance. Validate readiness and data
before reopening. Do not label `create_all` a migration engine. The model hash does
not certify arbitrary custom triggers, permissions or indexes against source code.
Restoration additionally compares reflected table schema to the original manifest.
No demonstration migration or new dependency was added in this sprint.

Rollback is restoration of the compatible backup AND files with matching application
code/dependencies into a new target. Arbitrary schema downgrade is not supported.
This workflow does not establish power-loss/fsync durability of the filesystem
bundle, online/WAL point-in-time recovery, cross-major upgrades or long-term retention.

References: [pg_dump](https://www.postgresql.org/docs/18/app-pgdump.html),
[pg_restore](https://www.postgresql.org/docs/18/app-pgrestore.html).
