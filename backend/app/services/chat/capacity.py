"""Two existing-session chats may share ONE legacy slot; other users stay exclusive.

Session locks and lifecycle permits must already be held. No queue, retry, or
Provider/Embedding capacity change. The legacy semaphore remains owned until the
last shared chat exits, including when the feature flag changes between requests.
"""
import threading

from fastapi import HTTPException


class Admission:
    def __init__(self):
        self._mutex = threading.Lock()
        self._shared = 0

    def acquire(self, slot, shared):
        with self._mutex:
            if shared:
                if self._shared >= 2 or (self._shared == 0 and not slot.acquire(blocking=False)):
                    raise HTTPException(429, "Local chat capacity is busy. Please try again shortly.")
                self._shared += 1
            elif not slot.acquire(blocking=False):
                raise HTTPException(429, "A chat is already running locally. Please try again shortly.")

    def release(self, slot, shared):
        with self._mutex:
            if shared:
                self._shared -= 1
                if self._shared:
                    return
            slot.release()

    def snapshot(self):
        with self._mutex:
            return {"shared": self._shared, "limit": 2}


admission = Admission()
