"""Process-local session mutexes; references include holders and waiters."""
from contextlib import contextmanager
from dataclasses import dataclass, field
import threading

from fastapi import HTTPException
from app.core.config import settings


@dataclass
class Entry:
    lock: object = field(default_factory=threading.RLock)
    references: int = 0


class Coordinator:
    def __init__(self):
        self._mutex = threading.Lock()
        self._entries = {}

    @contextmanager
    def hold(self, session_id, timeout=None):
        if session_id is None:
            yield
            return
        with self._mutex:
            entry = self._entries.setdefault(session_id, Entry())
            entry.references += 1
        acquired = False
        try:
            acquired = entry.lock.acquire(timeout=settings.SESSION_LOCK_WAIT_SECONDS if timeout is None else timeout)
            if not acquired:
                raise HTTPException(409, "Session update is still in progress. Please try again shortly.")
            yield
        finally:
            if acquired:
                entry.lock.release()
            with self._mutex:
                entry.references -= 1
                if entry.references == 0:
                    del self._entries[session_id]

    def snapshot(self):
        with self._mutex:
            return {key: entry.references for key, entry in self._entries.items()}


coordinator = Coordinator()
