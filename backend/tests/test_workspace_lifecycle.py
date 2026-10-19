"""Deterministic lifecycle interleavings, with no database/provider access."""
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
from unittest.mock import Mock, patch

import test_auth  # noqa: F401
from fastapi import HTTPException
from sqlalchemy.exc import DBAPIError
from app.services import workspace_lifecycle as life


class LifecycleTests(unittest.TestCase):
    def test_atomic_admission_or_delete(self):
        for _ in range(20):
            c = life.Coordinator()
            start = threading.Barrier(2)
            attempted = threading.Barrier(2)
            def run_chat():
                start.wait(5)
                token = None
                try:
                    token = c.admit("w", "r")
                    return "chat"
                except HTTPException as exc:
                    self.assertEqual(exc.status_code, 409)
                    return "rejected"
                finally:
                    attempted.wait(5)
                    if token:
                        c.release(token)
            def run_delete():
                start.wait(5)
                try:
                    with c.deletion():
                        attempted.wait(5)
                        return "delete"
                except HTTPException as exc:
                    self.assertEqual(exc.status_code, 409)
                    attempted.wait(5)
                    return "rejected"
            with ThreadPoolExecutor(max_workers=2) as pool:
                chat = pool.submit(run_chat)
                delete = pool.submit(run_delete)
                results = [chat.result(6), delete.result(6)]
            self.assertEqual(results.count("rejected"), 1)
            self.assertEqual(c.snapshot(), {"active": 0, "deleting": False, "uncertain": False})

    def test_rollback_remains_active(self):
        c = life.Coordinator()
        entered, release = threading.Event(), threading.Event()
        db = Mock()
        db.scalar.return_value = None
        def rollback():
            entered.set()
            self.assertTrue(release.wait(5))
        db.rollback.side_effect = rollback
        def execute():
            with life.chat_permit(db, "w", "r"):
                raise RuntimeError("commit failed")
        with patch.object(life, "coordinator", c), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(execute)
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(c.snapshot()["active"], 1)
                with self.assertRaises(HTTPException) as error, c.deletion():
                    self.fail("delete entered")
                self.assertEqual(error.exception.status_code, 409)
            finally:
                release.set()
            with self.assertRaises(RuntimeError):
                future.result(6)
        with c.deletion():
            pass
        self.assertEqual(c.snapshot()["active"], 0)

    def test_pending_recheck_releases_permit(self):
        c, db = life.Coordinator(), Mock()
        db.scalar.return_value = "pending"
        with patch.object(life, "coordinator", c), self.assertRaises(HTTPException) as error:
            with life.chat_permit(db, "w", "r"):
                self.fail("pending deletion admitted")
        self.assertEqual(error.exception.status_code, 503)
        db.rollback.assert_called_once()
        self.assertEqual(c.snapshot()["active"], 0)

    def test_uncertain_transaction_fails_closed_without_leaking_token(self):
        for rollback_fails in (False, True):
            c, db = life.Coordinator(), Mock()
            db.scalar.return_value = None
            error = DBAPIError("COMMIT", {}, RuntimeError("lost connection"), connection_invalidated=True)
            if rollback_fails:
                error = RuntimeError("generation error")
                db.rollback.side_effect = RuntimeError("rollback failed")
            with patch.object(life, "coordinator", c), self.assertRaises(Exception):
                with life.chat_permit(db, "w", "r"):
                    raise error
            self.assertEqual(c.snapshot(), {"active": 0, "deleting": False, "uncertain": True})
            with self.assertRaises(HTTPException) as denied:
                c.admit("w", "next")
            self.assertEqual(denied.exception.status_code, 503)
            with self.assertRaises(HTTPException), c.deletion():
                self.fail("uncertain deletion allowed")

    def test_delete_exception_releases_and_invalidated_outcome_blocks(self):
        c = life.Coordinator()
        with self.assertRaises(RuntimeError), c.deletion():
            raise RuntimeError("safe rejection")
        token = c.admit("w", "r")
        c.release(token)
        c.release(token)
        with self.assertRaises(DBAPIError), c.deletion():
            raise DBAPIError("COMMIT", {}, RuntimeError("lost"), connection_invalidated=True)
        self.assertEqual(c.snapshot(), {"active": 0, "deleting": False, "uncertain": True})
