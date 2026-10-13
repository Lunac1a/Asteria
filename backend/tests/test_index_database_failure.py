"""SQLite transaction characterization; no connection-loss/commit-ambiguity claims."""

import unittest
from unittest.mock import patch

import test_index_unexpected_exception as fixtures
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError, OperationalError, PendingRollbackError

from app.api.routes.knowledge import index_document, reindex
from app.models.knowledge import Chunk, Document


class IndexDatabaseFailureTests(unittest.TestCase):
    def setUp(self):
        fixtures.UnexpectedIndexExceptionTests.setUp(self)

    def seed(self, reindexing):
        fixtures.UnexpectedIndexExceptionTests.seed_document(
            self, "ready" if reindexing else "processing", old_chunk=reindexing
        )
        if reindexing:
            with self.factory() as db:
                db.add(Chunk(id="old-second", document_id="document", ordinal=1,
                             page=2, content="Second old content", vector=[0.5, 0.5]))
                db.get(Document, "document").chunk_count = 2
                db.commit()
        with self.factory() as db:
            return self.chunk_snapshot(db)

    def chunk_snapshot(self, db):
        return [(c.id, c.ordinal, c.page, c.content, c.vector)
                for c in db.query(Chunk).filter_by(document_id="document")
                .order_by(Chunk.ordinal).all()]

    def exercise(self, reindexing, insert_failure, cleanup):
        old_chunks = self.seed(reindexing)
        # A real SQLite statement error on the second new row, after the first insert.
        if insert_failure:
            with self.engine.begin() as connection:
                connection.execute(text("""
                    CREATE TRIGGER injected_insert_failure BEFORE INSERT ON document_chunks
                    WHEN NEW.content = 'new-2'
                    BEGIN SELECT RAISE(ABORT, 'Injected Chunk insert failure'); END
                """))
        events = []

        def record_rollback(connection):
            events.append("rollback")

        def record_statement(connection, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("INSERT INTO DOCUMENT_CHUNKS"):
                events.append("insert")

        event.listen(self.engine, "rollback", record_rollback)
        event.listen(self.engine, "after_cursor_execute", record_statement)
        self.addCleanup(event.remove, self.engine, "rollback", record_rollback)
        self.addCleanup(event.remove, self.engine, "after_cursor_execute", record_statement)
        failure = OperationalError("Synthetic pre-commit failure", {}, RuntimeError("Injected"))
        db = self.factory()
        self.addCleanup(db.close)
        original_commit = db.commit
        original_add = db.add
        injected = []

        def add(instance):
            original_add(instance)
            if insert_failure and isinstance(instance, Chunk):
                # Force separate SQL inserts: prove one row was written before failure.
                db.flush()

        def commit():
            document = db.get(Document, "document")
            if not insert_failure and document.status == "ready":
                db.flush()
                self.assertEqual(document.chunk_count, 2)
                self.assertIsNone(document.error)
                self.assertEqual([c[3] for c in self.chunk_snapshot(db)], ["new-1", "new-2"])
                injected.append(True)
                raise failure  # The database COMMIT has deliberately not been sent.
            original_commit()

        with (
            patch("app.api.routes.knowledge.run_worker", return_value={"chunks": [
                dict(page=1, content="new-1", vector=[1.0, 0.0]),
                dict(page=2, content="new-2", vector=[0.0, 1.0]),
            ]}) as worker,
            patch.object(db, "commit", side_effect=commit),
            patch.object(db, "add", side_effect=add),
            patch.object(db, "rollback", wraps=db.rollback) as rollback,
        ):
            expected = IntegrityError if insert_failure else OperationalError
            with self.assertRaises(expected) as caught:
                if reindexing:
                    reindex("workspace", "document", self.owner, db)
                else:
                    index_document(db, db.get(Document, "document"))
            worker.assert_called_once()
            rollback.assert_not_called()  # index_document has no DB-error handler.
            self.assertTrue(db.in_transaction())
            self.assertEqual(db.is_active, not insert_failure)
            self.assertGreaterEqual(events.count("insert"), 1)
            if insert_failure:
                self.assertIn("Injected Chunk insert failure", str(caught.exception))
                self.assertEqual(events.count("rollback"), 1)  # Flush's internal rollback.
                with self.assertRaises(PendingRollbackError):
                    db.query(Document).count()
            else:
                self.assertIs(caught.exception, failure)
                self.assertEqual(injected, [True])
                self.assertEqual(events.count("rollback"), 0)
            if cleanup == "rollback":
                db.rollback()
                rollback.assert_called_once()
                self.assertTrue(db.is_active)
            else:
                db.close()  # Same cleanup action as get_db's finally.
                rollback.assert_not_called()
            self.assertFalse(db.in_transaction())
            self.assertEqual(events.count("rollback"), 1)
        db.close()
        # Inspect only after rollback/close; StaticPool shares a single connection.
        with self.factory() as fresh:
            document = fresh.get(Document, "document")
            self.assertEqual((document.status, document.error, document.chunk_count),
                             ("processing", None, len(old_chunks)))
            self.assertEqual(self.chunk_snapshot(fresh), old_chunks)

    def test_first_index_insert_failure_then_session_close(self):
        self.exercise(False, True, "close")

    def test_reindex_insert_failure_then_session_close(self):
        self.exercise(True, True, "close")

    def test_first_index_insert_failure_then_explicit_rollback(self):
        self.exercise(False, True, "rollback")

    def test_reindex_insert_failure_then_explicit_rollback(self):
        self.exercise(True, True, "rollback")

    def test_first_index_precommit_failure_then_session_close(self):
        self.exercise(False, False, "close")

    def test_reindex_precommit_failure_then_session_close(self):
        self.exercise(True, False, "close")

    def test_first_index_precommit_failure_then_explicit_rollback(self):
        self.exercise(False, False, "rollback")

    def test_reindex_precommit_failure_then_explicit_rollback(self):
        self.exercise(True, False, "rollback")
