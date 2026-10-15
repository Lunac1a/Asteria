"""HTTP protocol tests; optional, explicitly isolated PostgreSQL test database.

Set ASTERIA_RECOVERY_TEST_URL and ASTERIA_RECOVERY_TEST_PGDATA for real PG tests.
The database name must be recovery_protocol; each test creates its own schema.
No normal application DATABASE_URL or local .env is used for the test database.
"""
import concurrent.futures
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

import test_dashboard
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.models.knowledge import Chunk, Document, Workspace
from app.models.workspace_delete_operation import WorkspaceDeleteOperation as Operation
from app.services import workspace_delete_recovery as recovery


class WorkspaceDeleteRecoveryTests(unittest.TestCase):
    def setUp(self):
        url = os.environ.get("ASTERIA_RECOVERY_TEST_URL")
        self.postgres = bool(url)
        if url:
            parsed = make_url(url)
            self.assertTrue(parsed.drivername.startswith("postgresql"))
            self.assertIn(parsed.host, {"127.0.0.1", "localhost", "::1"})
            self.assertEqual(parsed.database, "recovery_protocol")
            expected = os.environ["ASTERIA_RECOVERY_TEST_PGDATA"]
            admin = create_engine(url, poolclass=NullPool)
            self.addCleanup(admin.dispose)
            schema = "recovery_test_" + uuid.uuid4().hex
            with admin.begin() as db:
                self.assertEqual(db.scalar(text("SHOW data_directory")), expected)
                db.execute(text(f'CREATE SCHEMA "{schema}"'))

            def drop_owned_schema():
                with admin.begin() as db:
                    self.assertEqual(db.scalar(text("SHOW data_directory")), expected)
                    db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))

            self.addCleanup(drop_owned_schema)
            engine = create_engine(url, poolclass=NullPool,
                                   connect_args={"options": f"-csearch_path={schema}"})
            with patch.object(test_dashboard, "create_engine", return_value=engine):
                test_dashboard.DashboardTests.setUp(self)
        else:
            test_dashboard.DashboardTests.setUp(self)
        self.bind = self.factory.kw["bind"]
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parents[2] / "data")))
        self.enterContext(patch("app.api.routes.knowledge.data_root", return_value=self.root))
        (self.root / "uploads").mkdir()
        self.contents = [b"Synthetic alpha", b"Synthetic beta"]
        self.paths = [self.root / "uploads" / f"{i}.txt" for i in range(2)]
        for path, content in zip(self.paths, self.contents):
            path.write_bytes(content)
        with self.factory() as db:
            db.add(Workspace(id="workspace-a", user_id=self.users[0][0], name="Synthetic"))
            db.add(Workspace(id="workspace-b", user_id=self.users[1][0], name="Other"))
            db.flush()
            for index in range(2):
                db.add(Document(id=f"doc-{index}", workspace_id="workspace-a", name=f"{index}.txt",
                                storage_key=f"uploads/{index}.txt", size_bytes=len(self.contents[index]),
                                status="ready", embedding_model="test", chunk_count=1))
            db.flush()
            for index in range(2):
                db.add(Chunk(id=f"chunk-{index}", document_id=f"doc-{index}", ordinal=0, content="Synthetic", vector=[1.0]))
            db.commit()
        self.enterContext(patch.object(settings, "WORKSPACE_DELETE_MAINTENANCE_MODE", True))
        self.enterContext(patch.object(settings, "RECOVERY_SINGLE_PROCESS_CONFIRMED", True))

    def delete(self):
        return self.client.delete("/api/workspaces/workspace-a", headers=self.users[0][1])

    def operation(self):
        with self.factory() as db:
            return db.scalars(select(Operation)).one()

    def prepare(self):
        identifier = str(uuid.uuid4())
        with recovery.operation_gates():
            recovery.prepare(self.bind, "workspace-a", self.users[0][0], self.root, identifier)
        return identifier

    def staged(self, operation):
        return [self.root / item["staged"] for item in operation.manifest["files"]]

    def stage(self, identifier):
        with recovery.operation_gates(), self.factory() as db:
            operation = recovery.locked_operation(db, identifier)
            recovery.apply_files(operation, self.root, "stage")

    def assert_restored(self):
        self.assertEqual(self.operation().state, "RESTORED")
        for path, content in zip(self.paths, self.contents):
            self.assertEqual(path.read_bytes(), content)
        with self.factory() as db:
            self.assertIsNotNone(db.get(Workspace, "workspace-a"))
            self.assertEqual(db.query(Document).count(), 2)
            self.assertEqual(db.query(Chunk).count(), 2)
            self.assertIsNotNone(db.get(Workspace, "workspace-b"))
        self.assertFalse(any(path.exists() for path in self.staged(self.operation())))

    def assert_cleaned(self):
        self.assertEqual(self.operation().state, "CLEANED")
        with self.factory() as db:
            self.assertIsNone(db.get(Workspace, "workspace-a"))
            self.assertEqual(db.query(Document).count(), 0)
            self.assertEqual(db.query(Chunk).count(), 0)
            self.assertIsNotNone(db.get(Workspace, "workspace-b"))
        self.assertFalse(any(path.exists() for path in self.paths + self.staged(self.operation())))

    def commit_fault(self, state, after=False):
        original = Session.commit
        fired = []

        def commit(db):
            matches = any(isinstance(row, Operation) and row.state == state for row in list(db.new) + list(db.dirty))
            if matches and not fired:
                fired.append(True)
                if after:
                    original(db)
                raise RuntimeError(f"Synthetic {state} {'reply lost' if after else 'commit failure'}")
            return original(db)

        return patch.object(Session, "commit", commit)

    def test_prepared_is_durable_before_any_file_moves(self):
        identifier = self.prepare()
        operation = self.operation()
        self.assertEqual(operation.id, identifier)
        self.assertEqual(operation.state, "PREPARED")
        self.assertEqual(len(operation.manifest["files"]), 2)
        for item, path in zip(operation.manifest["files"], self.paths):
            self.assertEqual(item["sha256"], recovery.fingerprint(path)["sha256"])
        self.assertFalse(any(path.exists() for path in self.staged(operation)))
        recovery.recover_operation(self.bind, identifier, self.root)
        self.assert_restored()

    def test_prepare_commit_failure_has_no_file_side_effect(self):
        with self.commit_fault("PREPARED"), self.assertRaises(RuntimeError):
            self.delete()
        with self.factory() as db:
            self.assertEqual(db.query(Operation).count(), 0)
        self.assertEqual([p.read_bytes() for p in self.paths], self.contents)
        self.assertFalse((self.root / "deleted-files").exists())

    def test_prepare_lost_commit_reply_remains_recoverable(self):
        with self.commit_fault("PREPARED", after=True), self.assertRaises(RuntimeError):
            self.delete()
        self.assertEqual(self.operation().state, "PREPARED")
        self.assertEqual([p.read_bytes() for p in self.paths], self.contents)
        recovery.recover_operation(self.bind, self.operation().id, self.root)
        self.assert_restored()

    def test_normal_http_delete_finishes_cleaned(self):
        response = self.delete()
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        self.assert_cleaned()

    def test_precommit_failure_restores_files_and_records(self):
        with self.commit_fault("COMMITTED"), self.assertRaises(RuntimeError):
            self.delete()
        self.assert_restored()

    def test_lost_delete_commit_reply_uses_committed_marker(self):
        with self.commit_fault("COMMITTED", after=True):
            self.assertEqual(self.delete().status_code, 204)
        self.assert_cleaned()

    def test_cleanup_failure_leaves_committed_for_retry(self):
        original = Path.unlink

        def fail_staged(path, *args, **kwargs):
            if path.suffix == ".deleted":
                raise OSError("Synthetic cleanup failure")
            return original(path, *args, **kwargs)

        with patch.object(Path, "unlink", fail_staged), self.assertRaises(OSError):
            self.delete()
        operation = self.operation()
        self.assertEqual(operation.state, "COMMITTED")
        self.assertIn("Synthetic cleanup", operation.last_error)
        with self.factory() as db:
            self.assertIsNone(db.get(Workspace, "workspace-a"))
        self.assertTrue(all(p.exists() for p in self.staged(operation)))
        recovery.recover_operation(self.bind, operation.id, self.root)
        self.assert_cleaned()

    def test_repeated_recovery_is_noop_in_both_terminal_states(self):
        identifier = self.prepare()
        self.stage(identifier)
        self.assertEqual(recovery.recover_operation(self.bind, identifier, self.root), "RESTORED")
        self.assertEqual(recovery.recover_operation(self.bind, identifier, self.root), "RESTORED")
        self.assert_restored()
        self.assertEqual(self.delete().status_code, 204)
        with self.factory() as db:
            clean = db.scalar(select(Operation).where(Operation.state == "CLEANED"))
            clean_id = clean.id
        self.assertEqual(recovery.recover_operation(self.bind, clean_id, self.root), "CLEANED")

    def test_partial_staging_can_be_restored(self):
        identifier = self.prepare()
        target = self.staged(self.operation())[0]
        target.parent.mkdir(parents=True)
        recovery.move_no_replace(self.paths[0], target)
        recovery.recover_operation(self.bind, identifier, self.root)
        self.assert_restored()

    def test_recovery_interrupted_after_one_restore_is_repeatable(self):
        identifier = self.prepare()
        self.stage(identifier)
        original = recovery.move_no_replace
        calls = []

        def move(source, target):
            calls.append(True)
            if len(calls) == 2:
                raise OSError("Synthetic interruption after first restore")
            original(source, target)

        with patch.object(recovery, "move_no_replace", move), self.assertRaises(OSError):
            recovery.recover_operation(self.bind, identifier, self.root)
        self.assertEqual(self.operation().state, "PREPARED")
        self.assertTrue(self.paths[0].exists())
        recovery.recover_operation(self.bind, identifier, self.root)
        self.assert_restored()

    def test_terminal_commit_failure_after_file_restore_is_repeatable(self):
        identifier = self.prepare()
        self.stage(identifier)
        with self.commit_fault("RESTORED"), self.assertRaises(RuntimeError):
            recovery.recover_operation(self.bind, identifier, self.root)
        self.assertEqual(self.operation().state, "PREPARED")
        self.assertEqual([p.read_bytes() for p in self.paths], self.contents)
        recovery.recover_operation(self.bind, identifier, self.root)
        self.assert_restored()

    def test_terminal_commit_failure_after_cleanup_is_repeatable(self):
        with self.commit_fault("CLEANED"), self.assertRaises(RuntimeError):
            self.delete()
        self.assertEqual(self.operation().state, "COMMITTED")
        self.assertFalse(any(p.exists() for p in self.staged(self.operation())))
        recovery.recover_operation(self.bind, self.operation().id, self.root)
        self.assert_cleaned()

    def test_partial_cleanup_retries_only_remaining_files(self):
        original = Path.unlink
        calls = []

        def unlink(path, *args, **kwargs):
            if path.suffix == ".deleted":
                calls.append(path)
                if len(calls) == 2:
                    raise OSError("Synthetic failure after first cleanup")
            return original(path, *args, **kwargs)

        with patch.object(Path, "unlink", unlink), self.assertRaises(OSError):
            self.delete()
        operation = self.operation()
        self.assertEqual(operation.state, "COMMITTED")
        self.assertEqual([p.exists() for p in self.staged(operation)], [False, True])
        recovery.recover_operation(self.bind, operation.id, self.root)
        self.assert_cleaned()

    def test_lost_terminal_commit_reply_is_safe_to_retry(self):
        with self.commit_fault("CLEANED", after=True), self.assertRaises(RuntimeError):
            self.delete()
        self.assert_cleaned()
        self.assertEqual(recovery.recover_operation(self.bind, self.operation().id, self.root), "CLEANED")

    def test_root_change_and_foreign_file_reference_fail_closed(self):
        identifier = self.prepare()
        self.stage(identifier)
        other_root = self.root / "other-root"
        other_root.mkdir()
        with self.assertRaises(recovery.RecoveryConflict):
            recovery.recover_operation(self.bind, identifier, other_root)
        with self.factory() as db:
            db.add(Document(id="foreign-doc", workspace_id="workspace-b", name="Foreign",
                            storage_key="uploads/0.txt", size_bytes=1, status="ready", embedding_model="test"))
            db.commit()
        with self.assertRaises(recovery.RecoveryConflict):
            recovery.recover_operation(self.bind, identifier, self.root)
        self.assertFalse(any(p.exists() for p in self.paths))
        self.assertEqual([p.read_bytes() for p in self.staged(self.operation())], self.contents)

    def test_missing_schema_blocks_normal_resource_access(self):
        Operation.__table__.drop(self.bind)
        with patch.object(settings, "WORKSPACE_DELETE_MAINTENANCE_MODE", False):
            self.assertEqual(self.client.get("/api/workspaces", headers=self.users[0][1]).status_code, 503)
            self.assertEqual(self.delete().status_code, 503)
        self.assertEqual([p.read_bytes() for p in self.paths], self.contents)

    def test_both_paths_conflict_does_not_touch_any_file(self):
        identifier = self.prepare()
        self.stage(identifier)
        self.paths[1].write_bytes(self.contents[1])
        staged = self.staged(self.operation())
        with self.assertRaises(recovery.RecoveryConflict):
            recovery.recover_operation(self.bind, identifier, self.root)
        self.assertFalse(self.paths[0].exists())  # Whole-manifest preflight, no first-file restore.
        self.assertEqual([p.read_bytes() for p in staged], self.contents)
        self.assertEqual(self.operation().state, "PREPARED")
        self.assertIsNotNone(self.operation().last_error)

    def test_hash_mismatch_fails_closed(self):
        identifier = self.prepare()
        self.stage(identifier)
        staged = self.staged(self.operation())
        staged[1].write_bytes(b"Different file")
        with self.assertRaises(recovery.RecoveryConflict):
            recovery.recover_operation(self.bind, identifier, self.root)
        self.assertTrue(staged[0].exists())
        self.assertEqual(staged[1].read_bytes(), b"Different file")
        self.assertFalse(any(p.exists() for p in self.paths))

    def test_invalid_path_is_rejected_before_metadata_or_move(self):
        with self.factory() as db:
            db.get(Document, "doc-1").storage_key = "../outside.txt"
            db.commit()
        self.assertEqual(self.delete().status_code, 409)
        with self.factory() as db:
            self.assertEqual(db.query(Operation).count(), 0)
        self.assertEqual([p.read_bytes() for p in self.paths], self.contents)

    def test_no_replace_move_preserves_both_files(self):
        with self.assertRaises(OSError):
            recovery.move_no_replace(self.paths[0], self.paths[1])
        self.assertEqual([p.read_bytes() for p in self.paths], self.contents)

    def test_manifest_immutable_and_pending_workspace_unique(self):
        self.prepare()
        with self.factory() as db:
            row = db.get(Operation, self.operation().id)
            row.manifest = {"files": []}
            with self.assertRaises(ValueError):
                db.commit()
        old = self.operation()
        with self.factory() as db:
            db.add(Operation(id=str(uuid.uuid4()), workspace_id=old.workspace_id, owner_id=old.owner_id,
                             root=old.root, manifest=old.manifest, manifest_sha256=old.manifest_sha256, state="PREPARED"))
            with self.assertRaises(IntegrityError):
                db.commit()

    def test_maintenance_and_pending_operations_block_resource_requests(self):
        headers = self.users[0][1]
        self.assertEqual(self.client.get("/api/workspaces", headers=headers).status_code, 503)
        self.assertEqual(self.client.post("/api/workspaces", headers=headers, json={"name": "blocked"}).status_code, 503)
        self.prepare()
        with patch.object(settings, "WORKSPACE_DELETE_MAINTENANCE_MODE", False):
            self.assertEqual(self.client.get("/api/workspaces", headers=headers).status_code, 503)
            self.assertEqual(self.delete().status_code, 503)
        self.assertEqual(self.client.get("/api/me", headers=headers).status_code, 200)

    def test_deletion_is_disabled_without_explicit_maintenance(self):
        with patch.object(settings, "WORKSPACE_DELETE_MAINTENANCE_MODE", False):
            self.assertEqual(self.delete().status_code, 503)
        with patch.object(settings, "RECOVERY_SINGLE_PROCESS_CONFIRMED", False):
            self.assertEqual(self.delete().status_code, 503)
        self.assertEqual([p.read_bytes() for p in self.paths], self.contents)

    def test_external_transaction_cannot_weaken_prepare_commit(self):
        with self.bind.begin() as connection, self.assertRaises(recovery.RecoveryConflict):
            recovery.delete_workspace(connection, "workspace-a", self.users[0][0], self.root)
        self.assertEqual([p.read_bytes() for p in self.paths], self.contents)
        with self.factory() as db:
            self.assertEqual(db.query(Operation).count(), 0)

    def test_recovery_shares_both_nonreentrant_process_gates(self):
        from app.api.routes.knowledge import document_slot
        from app.api.routes.chat import chat_slot
        identifier = self.prepare()
        for gate in (document_slot, chat_slot):
            with gate, self.assertRaises(recovery.RecoveryConflict):
                recovery.recover_operation(self.bind, identifier, self.root)
        recovery.recover_operation(self.bind, identifier, self.root)
        self.assert_restored()

    def test_postgres_inflight_transaction_times_out_without_file_action(self):
        if not self.postgres:
            self.skipTest("Requires PostgreSQL row locking")
        identifier = self.prepare()
        self.stage(identifier)
        with self.factory() as owner:
            owner.scalar(select(Operation).where(Operation.id == identifier).with_for_update())
            started = time.monotonic()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(recovery.recover_operation, self.bind, identifier, self.root)
                with self.assertRaises(DBAPIError):
                    future.result(timeout=8)
            self.assertLess(time.monotonic() - started, 7)
            self.assertFalse(any(p.exists() for p in self.paths))
        recovery.recover_operation(self.bind, identifier, self.root)
        self.assert_restored()
