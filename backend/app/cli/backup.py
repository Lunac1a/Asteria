"""Offline PostgreSQL-container backup/restore. Explicit target; no default app DB."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess

from sqlalchemy import create_engine, text, inspect
from sqlalchemy.engine import make_url

from app.services.schema_compatibility import model_revision, validate_schema
from app.services.startup_recovery import storage_lease
from app.db.base import Base


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_file(root, key):
    value = PurePosixPath(key)
    if (not key or '\\' in key or ':' in key or value.is_absolute()
            or '..' in value.parts or value.as_posix() != key
            or value.parts[0] not in {'uploads', 'deleted-files'}):
        raise ValueError('Unsafe backup file reference')
    path = root.joinpath(*value.parts)
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError('Redirected backup path')
        if part == root:
            break
    if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
        raise ValueError('Backup requires exclusive regular files')
    return path


def docker(args, *, output=None, input_file=None):
    result = subprocess.run(['docker', *args], stdout=output or subprocess.PIPE,
                            stdin=input_file, stderr=subprocess.PIPE, timeout=180)
    if result.returncode:
        # pg tools may echo SQL/data. Do not expose their stderr in ordinary logs.
        raise RuntimeError('Docker/PostgreSQL tool failed; target remains offline')
    return result.stdout


def target(engine, container, database, user, expected_pgdata, expected_system_id):
    if not re.fullmatch(r'[0-9a-f]{64}', container):
        raise ValueError('Use an exact full Docker container ID')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', database) or database in {'postgres', 'template0', 'template1'}:
        raise ValueError('Select an explicit application database')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', user):
        raise ValueError('Invalid database role')
    with engine.connect() as db:
        identity = dict(database=db.scalar(text('SELECT current_database()')),
                        pgdata=db.scalar(text('SHOW data_directory')),
                        system_id=str(db.scalar(text('SELECT system_identifier FROM pg_control_system()'))),
                        server_version=db.scalar(text('SHOW server_version')))
    if identity['database'] != database or identity['pgdata'] != expected_pgdata or identity['system_id'] != expected_system_id:
        raise ValueError('Database target identity mismatch')
    raw = docker(['exec', container, 'psql', '-XAt', '-U', user, '-d', database, '-c',
                  "SELECT current_database() || '|' || current_setting('data_directory') || '|' || system_identifier FROM pg_control_system()"])
    if raw.decode().strip() != f"{database}|{expected_pgdata}|{expected_system_id}":
        raise ValueError('Docker and connection target differ')
    return identity


def references(db, root):
    if db.scalar(text("SELECT count(*) FROM workspace_delete_operations WHERE state IN ('PREPARED','COMMITTED')")):
        raise ValueError('Pending deletion must be recovered before backup/restore')
    if db.scalar(text("SELECT count(*) FROM documents WHERE status='processing'")):
        raise ValueError('Processing documents require recovery before backup')
    keys = set(db.scalars(text('SELECT storage_key FROM documents')))
    for manifest in db.scalars(text('SELECT manifest FROM workspace_delete_operations')):
        for item in manifest['files']:
            staged = item['staged']
            if safe_file(root, staged).exists():
                keys.add(staged)
    for key in keys:
        if not safe_file(root, key).is_file():
            raise ValueError('Required document file is missing')
    return sorted(keys)


def schema_snapshot(db):
    i = inspect(db)
    result = {name: {'columns': [(c['name'], str(c['type']), c['nullable']) for c in i.get_columns(name)],
                   'pk': i.get_pk_constraint(name), 'fk': i.get_foreign_keys(name),
                   'unique': i.get_unique_constraints(name), 'checks': i.get_check_constraints(name),
                   'indexes': i.get_indexes(name)} for name in sorted(i.get_table_names())}
    # pg_dump/restore moves varchar->text casts from arrays onto their elements.
    # Normalize only this project's simple text enum predicates, not arbitrary SQL.
    for table in result.values():
        for check in table['checks']:
            check['sqltext'] = enum_predicate(check['sqltext'])
        for index in table['indexes']:
            options = index.get('dialect_options', {})
            if 'postgresql_where' in options:
                options['postgresql_where'] = enum_predicate(options['postgresql_where'])
    return result


def enum_predicate(expression):
    if any(not re.fullmatch(r'[A-Za-z_]+', v) for v in re.findall(r"'([^']*)'", expression)):
        return expression
    normalized = expression.replace('::character varying', '').replace('::text[]', '').replace('::text', '')
    normalized = re.sub(r'[()\s]', '', normalized)
    if re.fullmatch(r"(?:state|status|session_type)=ANYARRAY\['[A-Za-z_]+'(?:,'[A-Za-z_]+')*\]", normalized):
        return normalized
    return expression


def canonical(value):
    return json.loads(json.dumps(value, sort_keys=True, default=str))


@contextmanager
def offline_database(engine):
    with engine.begin() as db:
        db.execute(text("SET LOCAL lock_timeout='3s'"))
        others = db.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()"))
        if others:
            raise ValueError('Other database sessions exist; stop all writers first')
        yield db


def backup(engine, container, database, user, root, destination, identity):
    root = root.absolute()
    if (root / '.restore-incomplete').exists():
        raise ValueError('Cannot back up an incomplete restore')
    destination = destination.absolute()
    if destination.resolve() != destination or destination.exists() or destination.is_relative_to(root):
        raise ValueError('Backup destination must be new and outside application storage')
    with storage_lease(root), offline_database(engine) as db:
        validate_schema(db)
        for table in Base.metadata.sorted_tables:
            db.execute(text(f'LOCK TABLE "{table.name}" IN SHARE MODE'))
        keys = references(db, root)
        manifest = {'format': 1, 'model_revision': model_revision(), 'created_utc': datetime.now(timezone.utc).isoformat(),
                    'source': identity, 'source_root': str(root), 'schema': canonical(schema_snapshot(db)),
                    'files': {}, 'secrets': 'Fernet/JWT/database credentials are NOT included'}
        destination.mkdir(parents=True)
        for key in keys:
            source = safe_file(root, key)
            info = {'bytes': source.stat().st_size, 'sha256': digest(source)}
            output = safe_file(destination / 'files', key)
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, output)
            if digest(output) != info['sha256']:
                raise ValueError('File changed during backup')
            manifest['files'][key] = info
        dump = destination / 'database.dump'
        with dump.open('xb') as stream:
            docker(['exec', container, 'pg_dump', '-Fc', '--no-owner', '--no-privileges', '-U', user, '-d', database], output=stream)
        for key, info in manifest['files'].items():
            if digest(safe_file(root, key)) != info['sha256']:
                raise ValueError('Source file changed during backup')
        manifest['database'] = {'bytes': dump.stat().st_size, 'sha256': digest(dump)}
        # Manifest published last. No manifest means no usable backup.
        (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


def verify(bundle):
    if bundle.resolve() != bundle.absolute() or bundle.is_symlink() or bundle.is_junction():
        raise ValueError('Redirected backup directory')
    manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8'))
    if manifest['format'] != 1 or manifest['model_revision'] != model_revision():
        raise ValueError('Backup/code schema revision mismatch')
    entries = [(bundle / 'database.dump', manifest['database'])]
    entries += [(safe_file(bundle / 'files', k), v) for k, v in manifest['files'].items()]
    for path, info in entries:
        if path.is_symlink() or not path.is_file() or path.stat().st_size != info['bytes'] or digest(path) != info['sha256']:
            raise ValueError('Backup component missing or corrupted')
    return manifest


def restore(engine, container, database, user, root, bundle, identity):
    manifest = verify(bundle)  # Validate everything before touching the target.
    root = root.absolute()
    if root.resolve() != root or root.exists() or root.is_relative_to(bundle.absolute()):
        raise ValueError('Restore storage must be a new directory outside backup')
    if (identity['system_id'], database) == (manifest['source']['system_id'], manifest['source']['database']):
        raise ValueError('Refusing restore into original database')
    if identity['server_version'].split('.')[0] != manifest['source']['server_version'].split('.')[0]:
        raise ValueError('This restore workflow requires matching PostgreSQL major versions')
    with offline_database(engine) as db:
        if db.scalar(text("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%'")):
            raise ValueError('Restore database is not empty')
        if db.scalar(text("SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema')")):
            raise ValueError('Restore database contains routines')
    root.mkdir(parents=True)
    marker = root / '.restore-incomplete'
    marker.write_text('Keep offline until restore verification succeeds.\n')
    with storage_lease(root):
        for key in manifest['files']:
            output = safe_file(root, key)
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(safe_file(bundle / 'files', key), output)
        with (bundle / 'database.dump').open('rb') as stream:
            docker(['exec', '-i', container, 'pg_restore', '--single-transaction', '--exit-on-error',
                    '--no-owner', '--no-privileges', '-U', user, '-d', database], input_file=stream)
        with engine.connect() as db:
            validate_schema(db)
            if canonical(schema_snapshot(db)) != manifest['schema']:
                raise ValueError('Restored schema differs from backup')
            keys = references(db, root)
            for key in keys:
                if digest(safe_file(root, key)) != manifest['files'][key]['sha256']:
                    raise ValueError('Restored file differs from backup')
        marker.unlink()
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['backup', 'restore', 'verify'])
    p.add_argument('--bundle', type=Path, required=True)
    p.add_argument('--storage', type=Path)
    p.add_argument('--container')
    p.add_argument('--database')
    p.add_argument('--user', default='postgres')
    p.add_argument('--expected-pgdata')
    p.add_argument('--expected-system-id')
    p.add_argument('--confirm-stopped', action='store_true')
    args = p.parse_args()
    if args.command == 'verify':
        verify(args.bundle)
    else:
        if not args.confirm_stopped or not all([args.storage, args.container, args.database, args.expected_pgdata, args.expected_system_id]):
            p.error('Explicit target identity, storage and stopped-writer attestation required')
        url = make_url(os.environ['ASTERIA_BACKUP_DATABASE_URL'])
        if url.host not in {'localhost', '127.0.0.1', '::1'} or not url.drivername.startswith('postgresql'):
            p.error('Only explicit loopback PostgreSQL is supported')
        engine = create_engine(url, connect_args={'connect_timeout': 5})
        try:
            identity = target(engine, args.container, args.database, args.user, args.expected_pgdata, args.expected_system_id)
            fn = backup if args.command == 'backup' else restore
            fn(engine, args.container, args.database, args.user, args.storage, args.bundle, identity)
        finally:
            engine.dispose()
    print('Backup operation completed and verified; no secrets printed.')


if __name__ == '__main__':
    main()
