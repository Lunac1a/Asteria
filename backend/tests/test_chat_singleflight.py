"""Registry ownership and failure semantics without scheduling sleeps."""
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
import uuid

import test_auth  # noqa: F401
from fastapi import HTTPException
from app.schemas.chat import ChatRequest
from app.services.chat.singleflight import Registry, fingerprint


class SingleflightTests(unittest.TestCase):
    def test_atomic_leader_registration(self):
        registry = Registry(per_request_limit=7)
        barrier = threading.Barrier(8)
        def join():
            barrier.wait(3)
            return registry.register("id", ("owner",))
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: join(), range(8)))
        self.assertEqual(sum(leader for _, leader in results), 1)
        leader = next(flight for flight, leader in results if leader)
        self.assertTrue(all(flight is leader if is_leader else flight.flight is leader for flight, is_leader in results))
        registry.complete("id", leader)
        for flight, is_leader in results:
            registry.wait(flight, .1)
            if not is_leader:
                registry.release(flight)
        self.assertEqual(registry.running_count(), 0)
        self.assertEqual(registry.follower_count(), 0)

    def test_timeout_never_releases_leader(self):
        registry = Registry()
        flight, _ = registry.register("id", ())
        with self.assertRaises(HTTPException) as error:
            registry.wait(flight, .001)
        self.assertEqual(error.exception.status_code, 504)
        self.assertEqual(registry.running_count(), 1)
        self.assertFalse(flight.done.is_set())
        follower, is_leader = registry.register("id", ())
        self.assertFalse(is_leader)
        registry.release(follower)
        registry.complete("id", flight)
        registry.wait(flight, .1)

    def test_failure_notifies_all_and_removes_entry(self):
        for cause, expected in ((HTTPException(429, "busy"), 429), (RuntimeError("secret DB text"), 503)):
            registry = Registry()
            flight, _ = registry.register("id", ())
            followers = [registry.register("id", ())[0] for _ in range(3)]
            registry.complete("id", flight, cause)
            for follower in followers:
                with self.assertRaises(HTTPException) as error:
                    registry.wait(follower, .1)
                self.assertEqual(error.exception.status_code, expected)
                self.assertNotIn("secret", str(error.exception.detail))
                registry.release(follower)
            self.assertEqual(registry.running_count(), 0)
            self.assertEqual(registry.follower_count(), 0)
            replacement, leader = registry.register("id", ())
            self.assertTrue(leader)
            # Old completion cannot accidentally remove a replacement flight.
            with self.assertRaises(RuntimeError):
                registry.complete("id", flight)
            registry.complete("id", replacement)

    def test_fingerprint_binds_user_and_all_parameters(self):
        owner = uuid.uuid4()
        payload = ChatRequest(message=" question ", workspace_id="w")
        original = fingerprint(payload, owner)
        self.assertEqual(original, fingerprint(payload.model_copy(update={"message": "question"}), owner))
        variants = [fingerprint(payload, uuid.uuid4())]
        for key, value in {"workspace_id": "other", "session_id": "session", "message": "different",
                           "session_type": "learning", "answer_mode": "document", "learning_mode": "socratic",
                           "ui_locale": "en"}.items():
            variants.append(fingerprint(payload.model_copy(update={key: value}), owner))
        registry = Registry()
        flight, _ = registry.register("id", original)
        for variant in variants:
            with self.assertRaises(HTTPException) as error:
                registry.register("id", variant)
            self.assertEqual(error.exception.status_code, 409)
        registry.complete("id", flight)
