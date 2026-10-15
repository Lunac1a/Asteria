"""Blocking, opt-in startup recovery for operator-controlled single-instance use."""
from contextlib import contextmanager
import logging
import os
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.workspace_delete_operation import PENDING, WorkspaceDeleteOperation as Operation
from app.services import workspace_delete_recovery as recovery

logger = logging.getLogger(__name__)


@contextmanager
def storage_lease(root):
    """Local OS lock held until shutdown; never unlink the lock file (inode race).

    This excludes cooperating processes on this host/root only, not legacy writers.
    Kernel process termination releases the lock without any Python finally block.
    """
    root = recovery.checked_root(root)
    path = root / ".asteria-process.lock"
    if path.is_symlink() or path.is_junction():
        raise recovery.RecoveryConflict("Redirected process lock")
    handle = path.open("a+b")
    try:
        if os.fstat(handle.fileno()).st_nlink != 1:
            raise recovery.RecoveryConflict("Shared process lock")
        if os.name == "nt":
            import msvcrt
            if not path.stat().st_size:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield root
    finally:
        handle.close()


def pending(bind):
    with Session(bind) as db:
        return list(db.scalars(select(Operation.id).where(
            Operation.state.in_(PENDING)).order_by(Operation.created_at, Operation.id)))


def recover_startup(bind, root):
    """Caller must hold storage_lease for the entire serving lifetime.

    No schema mutation or request-time recovery. Failure propagates to the gate.
    Schema provisioning remains an explicit administrator action via init_local.
    """
    recovery.require_maintenance()
    recovery.require_engine(bind)
    if bind.dialect.name != "postgresql":
        raise recovery.RecoveryConflict("Automatic recovery requires PostgreSQL")
    recovery.checked_root(Path(root))
    with bind.connect() as db:
        db.execute(text("SELECT 1"))
        # Verify every mapped table/column, including metadata, before file actions.
        for table in Base.metadata.sorted_tables:
            db.execute(select(table).limit(0))
    for identifier in pending(bind):
        state = recovery.recover_operation(bind, identifier, root)
        logger.info("Startup recovered operation %s to %s", identifier, state)
    if pending(bind):
        raise recovery.RecoveryConflict("Unfinished deletion remains after startup recovery")
