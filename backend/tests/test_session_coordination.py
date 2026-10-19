"""Bounded session locks and rollback lifetime, using deterministic events."""
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
from unittest.mock import Mock, patch

import test_auth  # noqa: F401
from fastapi import HTTPException
from app.services import session_coordination as sessions, workspace_lifecycle as life


class SessionLockTests(unittest.TestCase):
    def test_same_session_timeout_different_session_independent(self):
        c = sessions.Coordinator()
        with c.hold("a"):
            with ThreadPoolExecutor(max_workers=1) as pool:
                def probe():
                    with c.hold("b", .1):
                        pass
                    with self.assertRaises(HTTPException) as error, c.hold("a", .01):
                        self.fail("same session entered")
                    return error.exception.status_code
                self.assertEqual(pool.submit(probe).result(2), 409)
            self.assertEqual(c.snapshot(), {"a": 1})
        self.assertEqual(c.snapshot(), {})

    def test_reentrancy_exception_and_registry_reclamation(self):
        c = sessions.Coordinator()
        for i in range(100):
            with self.assertRaises(RuntimeError), c.hold(str(i)):
                with c.hold(str(i)):
                    self.assertEqual(c.snapshot(), {str(i): 2})
                    raise RuntimeError("injected")
            self.assertEqual(c.snapshot(), {})

    def test_same_session_atomic_mutation(self):
        c = sessions.Coordinator()
        barrier = threading.Barrier(8)
        value = [0]
        def increment(_):
            barrier.wait(5)
            with c.hold("same", 5):
                value[0] += 1
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(increment, range(8)))
        self.assertEqual(value, [8])
        self.assertEqual(c.snapshot(), {})

    def test_session_and_workspace_held_through_rollback(self):
        c, workspace = sessions.Coordinator(), life.Coordinator()
        entered, release = threading.Event(), threading.Event()
        db = Mock()
        db.scalar.return_value = None
        def rollback():
            entered.set()
            self.assertTrue(release.wait(5))
        db.rollback.side_effect = rollback
        def fail():
            with life.chat_permit(db, "w", "r", session_id="s"):
                raise RuntimeError("commit failed")
        with patch.object(sessions, "coordinator", c), patch.object(life, "coordinator", workspace), ThreadPoolExecutor(max_workers=1) as pool:
            task = pool.submit(fail)
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaises(HTTPException), c.hold("s", .01):
                    self.fail("entered before rollback")
                with self.assertRaises(HTTPException), workspace.deletion():
                    self.fail("delete entered")
            finally:
                release.set()
            with self.assertRaises(RuntimeError):
                task.result(5)
        self.assertEqual(c.snapshot(), {})
        self.assertEqual(workspace.snapshot()["active"], 0)
