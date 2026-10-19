"""Shared legacy-slot ownership, bounded admission and exclusive compatibility."""
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest

from fastapi import HTTPException
from app.services.chat.capacity import Admission


class ChatCapacityTests(unittest.TestCase):
    def test_two_shared_keep_legacy_exclusive_until_last_exit(self):
        gate, slot = Admission(), threading.BoundedSemaphore(1)
        gate.acquire(slot, True)
        gate.acquire(slot, True)
        for shared in (True, False):
            with self.assertRaises(HTTPException) as error:
                gate.acquire(slot, shared)
            self.assertEqual(error.exception.status_code, 429)
        self.assertFalse(slot.acquire(blocking=False))  # Learning/Delete legacy users.
        gate.release(slot, True)
        self.assertFalse(slot.acquire(blocking=False))
        gate.release(slot, True)
        self.assertTrue(slot.acquire(blocking=False))
        slot.release()
        self.assertEqual(gate.snapshot()["shared"], 0)

    def test_old_owner_prevents_shared_admission_without_leak(self):
        gate, slot = Admission(), threading.BoundedSemaphore(1)
        gate.acquire(slot, False)
        with self.assertRaises(HTTPException):
            gate.acquire(slot, True)
        self.assertEqual(gate.snapshot()["shared"], 0)
        gate.release(slot, False)
        gate.acquire(slot, True)
        gate.release(slot, True)

    def test_racing_joiners_cannot_exceed_two(self):
        gate, slot = Admission(), threading.BoundedSemaphore(1)
        barrier, release = threading.Barrier(8), threading.Event()
        outcomes, lock, all_decided = [], threading.Lock(), threading.Event()
        def run():
            barrier.wait(5)
            acquired = False
            try:
                gate.acquire(slot, True)
                acquired = True
            except HTTPException:
                pass
            with lock:
                outcomes.append(acquired)
                if len(outcomes) == 8:
                    all_decided.set()
            if acquired:
                try:
                    self.assertTrue(release.wait(5))
                finally:
                    gate.release(slot, True)
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(run) for _ in range(8)]
            try:
                self.assertTrue(all_decided.wait(5))
                self.assertEqual(sum(outcomes), 2)
            finally:
                release.set()
            for future in futures:
                future.result(5)
        self.assertEqual(gate.snapshot()["shared"], 0)
