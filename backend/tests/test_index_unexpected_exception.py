"""Isolated worker failure recovery and database failure boundary regressions.

Run from backend: .venv/Scripts/python.exe -B -m unittest discover -s tests
    -p test_index_unexpected_exception.py -v
"""

import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from test_auth import Base  # Synthetic settings before application imports.
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.exc import OperationalError
from fastapi import HTTPException

from app.api.routes.knowledge import index_document, reindex
from app.models.knowledge import Chunk, Document, Workspace
from app.models.users import User


class UnexpectedIndexExceptionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        with self.engine.connect() as connection:
            connection.execute(text("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, autoflush=False)
        temp_root = Path(__file__).resolve().parents[2] / "data" / "test-runs"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(dir=temp_root)))
        self.enterContext(patch("app.api.routes.knowledge.data_root", return_value=self.root))
        self.owner = uuid.uuid4()
        with self.factory() as db:
            db.add(User(id=self.owner, email="index-test@example.com", password_hash="synthetic"))
            db.flush()
            db.add(Workspace(id="workspace", user_id=self.owner, name="Synthetic"))
            db.commit()

    def seed_document(self, status, old_chunk=False):
        with self.factory() as db:
            db.add(Document(
                id="document", workspace_id="workspace", name="synthetic.txt",
                storage_key="uploads/synthetic.txt", size_bytes=1, status=status,
                error=None, chunk_count=int(old_chunk), embedding_model="test:synthetic",
            ))
            db.flush()
            if old_chunk:
                db.add(Chunk(id="old-chunk", document_id="document", ordinal=0,
                             page=None, content="Old synthetic content", vector=[1.0, 0.0]))
            db.commit()

    def inject_failure(self, operation):
        with self.factory() as db:
            with (
                patch("app.api.routes.knowledge.run_worker",
                      side_effect=RuntimeError("Injected failure")) as worker,
                patch("app.api.routes.knowledge.model_id") as model,
                patch.object(db, "rollback", wraps=db.rollback) as rollback,
            ):
                with self.assertRaisesRegex(RuntimeError, "^Injected failure$") as error:
                    operation(db)
                self.assertIs(error.exception, worker.side_effect)
                worker.assert_called_once()
                self.assertEqual(worker.call_args.args[0],
                                 {"path": str(self.root / "uploads/synthetic.txt")})
                rollback.assert_called_once()
                model.assert_not_called()
                # Worker failure occurs after the read transaction was committed.
                self.assertFalse(db.in_transaction())
                # Close like get_db's finally, before querying with a fresh Session.
            db.close()
            self.assertFalse(db.in_transaction())

    def assert_failed_and_retry(self):
        with self.factory() as fresh:
            document = fresh.get(Document, "document")
            self.assertEqual(document.status, "failed")
            self.assertEqual(document.error, "Indexing failed unexpectedly. Please try again.")
            self.assertNotIn("Injected failure", document.error)
            self.assertEqual(document.chunk_count, 0)
            self.assertEqual(document.embedding_model, "test:synthetic")
            chunks = fresh.query(Chunk).filter_by(document_id="document").all()
            self.assertEqual(chunks, [])
        with self.factory() as retry, patch("app.api.routes.knowledge.run_worker", return_value={
            "chunks": [dict(page=None, content="New synthetic content", vector=[0.0, 1.0])],
        }):
            result = reindex("workspace", "document", self.owner, retry)
            self.assertEqual(result.status, "ready")
        with self.factory() as fresh:
            document = fresh.get(Document, "document")
            self.assertEqual(document.status, "ready")
            self.assertIsNone(document.error)
            self.assertEqual(document.chunk_count, 1)
            chunk = fresh.query(Chunk).filter_by(document_id="document").one()
            self.assertEqual(chunk.content, "New synthetic content")
            self.assertEqual(chunk.vector, [0.0, 1.0])
            self.assertNotEqual(chunk.id, "old-chunk")

    def test_first_index_runtime_error_marks_failed_and_retry_recovers(self):
        self.seed_document("processing")
        self.inject_failure(lambda db: index_document(db, db.get(Document, "document")))
        self.assert_failed_and_retry()

    def test_reindex_runtime_error_clears_old_chunks_and_retry_recovers(self):
        self.seed_document("ready", old_chunk=True)
        self.inject_failure(lambda db: reindex("workspace", "document", self.owner, db))
        self.assert_failed_and_retry()

    def test_http_exception_preserves_handled_failure_behavior(self):
        self.seed_document("ready", old_chunk=True)
        with self.factory() as db, patch("app.api.routes.knowledge.run_worker",
                                         side_effect=HTTPException(408, "Synthetic timeout")):
            result = reindex("workspace", "document", self.owner, db)
            self.assertEqual(result.status, "failed")
        with self.factory() as fresh:
            document = fresh.get(Document, "document")
            self.assertEqual((document.status, document.error, document.chunk_count),
                             ("failed", "Synthetic timeout", 0))
            self.assertEqual(fresh.query(Chunk).count(), 0)

    def test_result_commit_failure_is_not_classified_as_worker_failure(self):
        self.seed_document("processing", old_chunk=True)
        failure = OperationalError("Synthetic commit", {}, RuntimeError("Database unavailable"))
        with self.factory() as db:
            original_commit = db.commit
            calls = 0

            def commit():
                nonlocal calls
                calls += 1
                if calls == 2:
                    db.flush()  # Exercise rollback after SQL writes, before commit.
                    raise failure
                original_commit()

            with (
                patch.object(db, "commit", side_effect=commit),
                patch("app.api.routes.knowledge.run_worker", return_value={
                    "chunks": [dict(page=None, content="Uncommitted content", vector=[0.0, 1.0])],
                }),
                patch.object(db, "rollback", wraps=db.rollback) as rollback,
            ):
                with self.assertRaises(OperationalError) as error:
                    index_document(db, db.get(Document, "document"))
                self.assertIs(error.exception, failure)
                self.assertEqual(calls, 2)
                rollback.assert_not_called()
            # Request-session close rolls back the uncommitted result transaction.
        with self.factory() as fresh:
            document = fresh.get(Document, "document")
            self.assertEqual((document.status, document.error, document.chunk_count),
                             ("processing", None, 1))
            chunk = fresh.query(Chunk).one()
            self.assertEqual(chunk.id, "old-chunk")
            self.assertEqual(chunk.content, "Old synthetic content")
