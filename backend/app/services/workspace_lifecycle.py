"""Process-local, global deletion barrier; no persistent recovery state changes."""
from contextlib import contextmanager, ExitStack
from functools import wraps
from inspect import signature
import threading

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.models.workspace_delete_operation import PENDING, WorkspaceDeleteOperation
from app.models.knowledge import SessionWorkspace
from app.services import session_coordination


class Coordinator:
    def __init__(self):
        self._lock = threading.Lock()
        self._active = {}
        self._deleting = False
        self._uncertain = False

    def admit(self, workspace_id, request_id):
        with self._lock:
            if self._uncertain:
                raise HTTPException(503, "Workspace lifecycle requires transaction outcome verification")
            if self._deleting:
                raise HTTPException(409, "Workspace deletion/recovery is in progress")
            token = object()
            self._active[token] = (workspace_id, request_id)
            return token

    def release(self, token):
        with self._lock:
            self._active.pop(token, None)

    def uncertain(self):
        # Do not claim safe cleanup if rollback/connection outcome is unknown.
        # No automatic reset: operator verification and process restart required.
        with self._lock:
            self._uncertain = True

    @contextmanager
    def deletion(self):
        with self._lock:
            if self._uncertain:
                raise HTTPException(503, "Workspace lifecycle requires transaction outcome verification")
            if self._deleting or self._active:
                raise HTTPException(409, "Wait for active chat transactions to finish")
            self._deleting = True
        try:
            yield
        except DBAPIError as exc:
            if exc.connection_invalidated:
                self.uncertain()
            raise
        finally:
            with self._lock:
                self._deleting = False

    def snapshot(self):
        with self._lock:
            return {"active": len(self._active), "deleting": self._deleting,
                    "uncertain": self._uncertain}


coordinator = Coordinator()


@contextmanager
def chat_permit(db, workspace_id, request_id, session_id=None):
    token = coordinator.admit(workspace_id, request_id)
    locks = ExitStack()
    try:
        try:
            locks.enter_context(session_coordination.coordinator.hold(session_id))
            # A prior owner may have failed closed while this request waited.
            if coordinator.snapshot()["uncertain"]:
                raise HTTPException(503, "Workspace lifecycle requires transaction outcome verification")
            # Recheck after atomic admission: the request's earlier HTTP gate
            # may have run before a deletion/recovery operation completed.
            if db.scalar(select(WorkspaceDeleteOperation.id).where(
                    WorkspaceDeleteOperation.state.in_(PENDING)).limit(1)):
                raise HTTPException(503, "Pending workspace deletion requires manual recovery")
            yield
        except BaseException as exc:
            if isinstance(exc, DBAPIError) and exc.connection_invalidated:
                coordinator.uncertain()
            try:
                db.rollback()
            except BaseException:
                coordinator.uncertain()
                raise
            raise
    finally:
        try:
            locks.close()  # after commit or rollback, before lifecycle release
        finally:
            coordinator.release(token)


def session_operation(function):
    """Wrap the entire synchronous endpoint, including lazy Learning GET writes.

    RLock supports existing same-thread calls to Learning read() from mutations.
    It does not grant reentrancy to a different HTTP request/thread.
    """
    parameters = signature(function)
    @wraps(function)
    def protected(*args, **kwargs):
        values = parameters.bind(*args, **kwargs).arguments
        db, session_id = values["db"], values["session_id"]
        binding = db.get(SessionWorkspace, session_id)
        with chat_permit(db, binding.workspace_id if binding else None,
                         "session:" + session_id, session_id=session_id):
            return function(*args, **kwargs)
    return protected
