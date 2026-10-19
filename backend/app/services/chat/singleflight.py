"""Process-local coordination only; completed answers remain in PostgreSQL."""
from dataclasses import dataclass, field
import threading

from fastapi import HTTPException
from app.core.config import settings


@dataclass
class Flight:
    fingerprint: tuple
    done: threading.Event = field(default_factory=threading.Event)
    failure: tuple | None = None
    followers: int = 0


@dataclass(eq=False)
class Follower:
    flight: Flight
    request_id: str
    released: bool = False


class Registry:
    def __init__(self, per_request_limit=None, global_limit=None):
        self._lock = threading.Lock()
        self._running = {}
        self._followers = 0
        self._followers_by_request = {}
        self._per_request_limit = (settings.CHAT_SINGLEFLIGHT_PER_REQUEST_LIMIT
                                   if per_request_limit is None else per_request_limit)
        self._global_limit = (settings.CHAT_SINGLEFLIGHT_GLOBAL_LIMIT
                              if global_limit is None else global_limit)

    def register(self, request_id, fingerprint):
        with self._lock:
            flight = self._running.get(request_id)
            if flight is not None:
                if flight.fingerprint != fingerprint:
                    raise HTTPException(409, "Request ID already used for a different turn")
                request_followers = self._followers_by_request.get(request_id, 0)
                if request_followers >= self._per_request_limit or self._followers >= self._global_limit:
                    raise HTTPException(429, "Too many requests waiting for a chat result. Please try again shortly.")
                follower = Follower(flight, request_id)
                flight.followers += 1
                self._followers_by_request[request_id] = request_followers + 1
                self._followers += 1
                return follower, False
            flight = Flight(fingerprint)
            self._running[request_id] = flight
            return flight, True

    def complete(self, request_id, flight, error=None):
        with self._lock:
            if self._running.get(request_id) is not flight:
                raise RuntimeError("Chat flight ownership changed")
            if isinstance(error, HTTPException):
                flight.failure = (error.status_code, error.detail, error.headers)
            elif error is not None:
                # Never retain exception tracebacks, credentials or DB error text.
                flight.failure = (503, "Chat request failed. Please retry.", None)
            del self._running[request_id]
            flight.done.set()

    def wait(self, flight, timeout):
        # Event waiting is bounded and does not hold the registry lock.
        # A timed-out follower never changes the leader's ownership or lifecycle.
        if isinstance(flight, Follower):
            flight = flight.flight
        if not flight.done.wait(timeout):
            raise HTTPException(504, "Timed out waiting for the current chat request. The original request may still complete.")
        if flight.failure is not None:
            status, detail, headers = flight.failure
            raise HTTPException(status, detail, headers=headers)

    def release(self, follower):
        # A reservation belongs to this flight even after leader completion and
        # a new flight with the same request_id. Repeated cleanup is harmless.
        with self._lock:
            if not follower.released:
                follower.released = True
                follower.flight.followers -= 1
                self._followers -= 1
                remaining = self._followers_by_request[follower.request_id] - 1
                if remaining:
                    self._followers_by_request[follower.request_id] = remaining
                else:
                    del self._followers_by_request[follower.request_id]

    def follower_count(self):
        with self._lock:
            return self._followers

    def running_count(self):
        with self._lock:
            return len(self._running)


registry = Registry()


def fingerprint(payload, user_id):
    # Include ownership and every semantic request field. Only whitespace in the
    # question is normalized, as in the persisted replay check. Null session_id
    # is meaningful: a new-session request is not an existing-session request.
    return (str(user_id), payload.workspace_id, payload.session_id, payload.message.strip(),
            payload.session_type, payload.answer_mode, payload.learning_mode, payload.ui_locale)
