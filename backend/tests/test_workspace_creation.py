"""HTTP failure cleanup and quota regressions; SQLite does not verify row locks."""
import unittest
from unittest.mock import patch

import test_dashboard
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from app.models.knowledge import Workspace


class WorkspaceCreationTests(unittest.TestCase):
    def setUp(self):
        test_dashboard.DashboardTests.setUp(self)

    def test_commit_failure_rolls_back_and_next_request_succeeds(self):
        owner, headers = self.users[0]
        rolled_back = []

        def observed(session):
            rolled_back.append(True)

        event.listen(Session, "after_rollback", observed)
        self.addCleanup(event.remove, Session, "after_rollback", observed)

        def failed_commit(db):
            db.flush()  # Exercise cleanup after the INSERT has actually run.
            raise OperationalError("COMMIT", {}, RuntimeError("injected"))

        with patch.object(Session, "commit", failed_commit):
            with self.assertRaises(OperationalError):
                self.client.post("/api/workspaces", json={"name": "failed"}, headers=headers)
        self.assertTrue(rolled_back)
        with self.factory() as db:
            self.assertEqual(db.query(Workspace).filter_by(user_id=owner).count(), 0)
        response = self.client.post("/api/workspaces", json={"name": "retry"}, headers=headers)
        self.assertEqual(response.status_code, 200)

    def test_quota_rejection_rolls_back_transaction(self):
        owner, headers = self.users[0]
        with self.factory() as db:
            db.add_all([Workspace(user_id=owner, name=str(i)) for i in range(100)])
            db.commit()
        rolled_back = []

        def observed(session):
            rolled_back.append(True)

        event.listen(Session, "after_rollback", observed)
        self.addCleanup(event.remove, Session, "after_rollback", observed)
        response = self.client.post("/api/workspaces", json={"name": "overflow"}, headers=headers)
        self.assertEqual(response.status_code, 409)
        self.assertTrue(rolled_back)
        with self.factory() as db:
            self.assertEqual(db.query(Workspace).filter_by(user_id=owner).count(), 100)
