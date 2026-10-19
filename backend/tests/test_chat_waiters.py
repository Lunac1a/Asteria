"""Deterministic capacity/cleanup checks; no provider or database connections."""
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
from unittest.mock import Mock, patch

import test_auth  # noqa: F401
from fastapi import HTTPException
from app.api.routes import chat
from app.schemas.chat import ChatRequest
from app.services.chat.singleflight import Registry, fingerprint

OBSERVATIONS = []


class BoundedWaiterTests(unittest.TestCase):
    def status(self, action, expected):
        with self.assertRaises(HTTPException) as error:
            action()
        self.assertEqual(error.exception.status_code, expected)

    def test_per_request_and_conflict_precedence(self):
        r = Registry(2, 8)
        leader, _ = r.register("a", ())
        followers = [r.register("a", ())[0] for _ in range(2)]
        self.status(lambda: r.register("a", ()), 429)
        self.status(lambda: r.register("a", ("different",)), 409)
        self.assertEqual((leader.followers, r.follower_count()), (2, 2))
        self.assertFalse(leader.done.is_set())
        for follower in followers:
            r.release(follower)
        r.complete("a", leader)
        OBSERVATIONS.append({"case": "per_request", "admitted": 2, "overflow": 429, "conflict": 409, "remaining": r.follower_count()})

    def test_global_atomic_race_and_duplicate_release(self):
        r = Registry(4, 5)
        leaders = {key: r.register(key, ())[0] for key in ("a", "b", "c")}
        barrier = threading.Barrier(12)
        def compete(i):
            barrier.wait(5)
            try:
                return r.register("abc"[i % 3], ())[0]
            except HTTPException as error:
                self.assertEqual(error.status_code, 429)
                return None
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(compete, range(12)))
            admitted = [f for f in results if f is not None]
            self.assertEqual(len(admitted), 5)
            self.assertEqual(r.follower_count(), 5)
            self.assertTrue(all(f.followers <= 4 for f in leaders.values()))
            # Concurrent duplicate cleanup must not decrement either counter twice.
            list(pool.map(r.release, admitted * 4))
        self.assertEqual(r.follower_count(), 0)
        self.assertTrue(all(f.followers == 0 for f in leaders.values()))
        for key, flight in leaders.items():
            r.complete(key, flight)
        OBSERVATIONS.append({"case": "global_race", "contenders": 12, "admitted": 5, "rejected": 7, "remaining": 0})

    def test_per_request_atomic_race(self):
        r = Registry(2, 8)
        leader, _ = r.register("a", ())
        barrier = threading.Barrier(12)
        def compete(_):
            barrier.wait(5)
            try:
                return r.register("a", ())[0]
            except HTTPException as error:
                self.assertEqual(error.status_code, 429)
                return None
        with ThreadPoolExecutor(max_workers=12) as pool:
            admitted = [f for f in pool.map(compete, range(12)) if f is not None]
        self.assertEqual((len(admitted), leader.followers, r.follower_count()), (2, 2, 2))
        for follower in admitted:
            r.release(follower)
        r.complete("a", leader)
        OBSERVATIONS.append({"case": "per_request_race", "contenders": 12, "admitted": 2, "rejected": 10, "remaining": r.follower_count()})

    def test_timeout_releases_capacity_without_canceling_leader(self):
        r = Registry(1, 1)
        leader, _ = r.register("a", ())
        follower, _ = r.register("a", ())
        try:
            self.status(lambda: r.wait(follower, .001), 504)
        finally:
            r.release(follower)
        self.assertFalse(leader.done.is_set())
        replacement, is_leader = r.register("a", ())
        self.assertFalse(is_leader)
        self.assertEqual(r.follower_count(), 1)
        r.complete("a", leader)
        r.wait(replacement, .1)
        r.release(replacement)
        self.assertEqual(r.follower_count(), 0)
        OBSERVATIONS.append({"case": "timeout", "status": 504, "replacement_admitted": True, "remaining": 0})

    def test_success_and_failure_wake_followers(self):
        for error, expected in ((None, 200), (HTTPException(502, "injected"), 502), (RuntimeError("commit"), 503)):
            r = Registry(3, 3)
            leader, _ = r.register("a", ())
            followers = [r.register("a", ())[0] for _ in range(3)]
            barrier = threading.Barrier(4)
            def consume(follower):
                barrier.wait(5)
                try:
                    r.wait(follower, 5)
                    return 200
                except HTTPException as exc:
                    return exc.status_code
                finally:
                    r.release(follower)
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = [pool.submit(consume, f) for f in followers]
                barrier.wait(5)
                r.complete("a", leader, error)
                statuses = [future.result(6) for future in futures]
            self.assertEqual(statuses, [expected] * 3)
            self.assertEqual((r.running_count(), r.follower_count(), leader.followers), (0, 0, 0))
            OBSERVATIONS.append({"case": "completion", "statuses": statuses, "remaining": 0})

    def test_old_completion_does_not_release_new_reservation(self):
        r = Registry(1, 2)
        old, _ = r.register("a", ())
        follower, _ = r.register("a", ())
        r.complete("a", old)
        new, _ = r.register("a", ())
        # Completed old followers still own their request-ID budget until they
        # leave waiting; a replacement generation must not bypass that budget.
        self.status(lambda: r.register("a", ()), 429)
        r.release(follower)
        new_follower, _ = r.register("a", ())
        r.release(follower)
        self.assertEqual((old.followers, new.followers, r.follower_count()), (0, 1, 1))
        r.release(new_follower)
        r.complete("a", new)
        self.assertEqual(r._followers_by_request, {})

    def test_endpoint_releases_on_rollback_wait_and_replay_errors(self):
        for stage in ("rollback", "wait", "replay"):
            r = Registry(1, 1)
            payload = ChatRequest(message="question", workspace_id="w")
            leader, _ = r.register(str(payload.request_id), fingerprint(payload, "user"))
            db = Mock()
            failure = RuntimeError("injected " + stage)
            if stage == "rollback":
                db.rollback.side_effect = failure
            with patch.object(chat, "registry", r), patch.object(r, "wait", side_effect=failure if stage == "wait" else None), patch.object(chat, "persisted_response", side_effect=failure):
                with self.assertRaises(RuntimeError):
                    chat.chat(payload, Mock(), "user", db)
            self.assertEqual((r.follower_count(), leader.followers), (0, 0))
            r.complete(str(payload.request_id), leader)
            OBSERVATIONS.append({"case": "endpoint_exception", "stage": stage, "remaining": 0})

    def test_zero_capacity_still_allows_leaders_and_conflicts(self):
        for per_request, total in ((0, 8), (4, 0)):
            r = Registry(per_request, total)
            leader, _ = r.register("a", ())
            self.status(lambda: r.register("a", ()), 429)
            self.status(lambda: r.register("a", ("different",)), 409)
            r.complete("a", leader)
            self.assertEqual(r.follower_count(), 0)
