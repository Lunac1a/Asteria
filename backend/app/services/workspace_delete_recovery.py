"""Single-process, maintenance-only deletion protocol. No background/startup work.

Public delete/recover own both process gates; private helpers never reacquire them.
PostgreSQL row locks resolve in-flight transaction outcomes, not multi-instance work.
"""
import ctypes
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import uuid

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.chat_sessions import ChatSession
from app.models.knowledge import Chunk, Document, SessionWorkspace, Workspace
from app.models.workspace_delete_operation import PENDING, WorkspaceDeleteOperation as Operation
from app.services.deletion_service import delete_sessions
from app.services.knowledge.retrieval import owned_workspace

logger = logging.getLogger(__name__)


class RecoveryConflict(HTTPException):
    def __init__(self, message):
        super().__init__(409, message)


def require_maintenance():
    if not (settings.WORKSPACE_DELETE_MAINTENANCE_MODE and settings.RECOVERY_SINGLE_PROCESS_CONFIRMED):
        raise HTTPException(503, "Workspace deletion/recovery requires single-process maintenance mode")


def require_engine(bind):
    if not isinstance(bind, Engine):
        raise RecoveryConflict("Recovery requires an Engine, not an externally managed transaction")


@contextmanager
def operation_gates():
    from app.api.routes.knowledge import document_slot
    from app.api.routes.chat import chat_slot
    require_maintenance()
    if not document_slot.acquire(blocking=False):
        raise RecoveryConflict("Wait for document processing to finish")
    acquired_chat = False
    try:
        acquired_chat = chat_slot.acquire(blocking=False)
        if not acquired_chat:
            raise RecoveryConflict("Wait for the current answer to finish")
        yield
    finally:
        if acquired_chat:
            chat_slot.release()
        document_slot.release()


def digest(manifest):
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def checked_root(root):
    root = Path(root).absolute()
    if not root.is_dir() or root.resolve() != root:
        raise RecoveryConflict("Storage root is missing or redirected")
    for part in (root, *root.parents):
        if part.is_symlink() or part.is_junction():
            raise RecoveryConflict("Redirected storage path")
    return root


def checked_path(root, key, prefix):
    relative = PurePosixPath(key)
    if (not key or "\\" in key or ":" in key or relative.is_absolute()
            or ".." in relative.parts or relative.as_posix() != key
            or not key.startswith(prefix + "/")):
        raise RecoveryConflict("Invalid recovery file path")
    path = root.joinpath(*relative.parts)
    for part in (path, *path.parents):
        if part == root:
            break
        if part.is_symlink() or part.is_junction():
            raise RecoveryConflict("Redirected recovery file path")
    if path.resolve() != path or not path.is_relative_to(root):
        raise RecoveryConflict("Recovery path escapes storage")
    if path.exists():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RecoveryConflict("Recovery requires an exclusive regular file")
    return path


def fingerprint(path):
    with path.open("rb") as source:
        value = hashlib.file_digest(source, "sha256").hexdigest()
    return {"size": path.stat().st_size, "sha256": value}


def verify(path, item):
    if fingerprint(path) != {"size": item["size"], "sha256": item["sha256"]}:
        raise RecoveryConflict("Recovery file content differs from its recorded baseline")


def path_identity(root, key):
    return os.path.normcase(str((Path(root) / key).resolve()))


def exclusive_documents(db, root, entries, prepared):
    """Reject aliases/references even when a second record uses different spelling."""
    references = {}
    for identifier, key in db.execute(select(Document.id, Document.storage_key)):
        references.setdefault(path_identity(root, key), []).append(identifier)
    seen = set()
    for item in entries:
        original = path_identity(root, item["original"])
        staged = path_identity(root, item["staged"])
        expected = [item["document_id"]] if prepared else []
        if (original in seen or references.get(original, []) != expected
                or references.get(staged)):
            raise RecoveryConflict("File ownership is shared or changed")
        seen.add(original)


def move_no_replace(source, target):
    """Atomic no-replace rename on supported local Windows/Linux filesystems."""
    if os.name == "nt":
        os.rename(source, target)  # Windows rename fails if the target exists.
    elif sys.platform == "linux":
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, "renameat2", None)
        if rename is None:
            raise RecoveryConflict("No atomic no-replace rename available")
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1):
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))
    else:
        raise RecoveryConflict("Unsupported filesystem platform for recovery")


def locked_operation(db, operation_id):
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SET LOCAL lock_timeout = '1500ms'"))
    operation = db.scalar(select(Operation).where(Operation.id == operation_id).with_for_update())
    if operation is None:
        raise RecoveryConflict("Recovery operation not found; no file action is safe")
    return operation


def plan_files(operation, root, phase):
    if str(checked_root(root)) != operation.root or digest(operation.manifest) != operation.manifest_sha256:
        raise RecoveryConflict("Recovery root or immutable manifest changed")
    actions = []
    for item in operation.manifest["files"]:
        source = checked_path(root, item["original"], "uploads")
        target = checked_path(root, item["staged"], f"deleted-files/{operation.id}")
        if source.exists() and target.exists():
            raise RecoveryConflict("Original and staged files both exist")
        for path in (source, target):
            if path.exists():
                if not item["existed"]:
                    raise RecoveryConflict("A file appeared at an originally missing path")
                verify(path, item)
        if phase == "stage":
            if target.exists() or source.exists() != item["existed"]:
                raise RecoveryConflict("File baseline changed before staging")
            action = "stage" if item["existed"] else "skip"
        elif phase == "restore":
            if item["existed"] and not source.exists() and not target.exists():
                raise RecoveryConflict("Both file locations are missing")
            action = "restore" if target.exists() else "skip"
        else:
            if source.exists():
                raise RecoveryConflict("Committed deletion has an unexpected original file")
            action = "clean" if target.exists() else "skip"
        actions.append((item, source, target, action))
    return actions


def apply_files(operation, root, phase):
    # Whole-manifest preflight detects conflicts before any destructive action.
    actions = plan_files(operation, root, phase)
    for item, source, target, action in actions:
        checked_path(root, item["original"], "uploads")
        checked_path(root, item["staged"], f"deleted-files/{operation.id}")
        if action == "stage":
            verify(source, item)
            target.parent.mkdir(parents=True, exist_ok=True)
            checked_path(root, item["staged"], f"deleted-files/{operation.id}")
            move_no_replace(source, target)
        elif action == "restore":
            verify(target, item)
            source.parent.mkdir(parents=True, exist_ok=True)
            checked_path(root, item["original"], "uploads")
            move_no_replace(target, source)
        elif action == "clean":
            verify(target, item)
            target.unlink()


def prepare(bind, workspace_id, owner_id, root, operation_id):
    """Private protocol stage; caller holds both gates. T1 has its own Session."""
    require_engine(bind)
    root = checked_root(root)
    with Session(bind, expire_on_commit=False) as db:
        workspace = owned_workspace(db, workspace_id, owner_id)
        if db.scalar(select(Operation.id).where(Operation.state.in_(PENDING)).limit(1)):
            raise RecoveryConflict("Pending deletion must be recovered first")
        documents = db.scalars(select(Document).where(Document.workspace_id == workspace_id).order_by(Document.id)).all()
        sessions = db.scalars(select(ChatSession).join(SessionWorkspace, SessionWorkspace.session_id == ChatSession.id).where(SessionWorkspace.workspace_id == workspace_id)).all()
        if any(row.user_id != owner_id for row in sessions):
            raise RecoveryConflict("Workspace contains an inconsistent conversation binding")
        entries = []
        for index, document in enumerate(documents):
            age = (datetime.now(timezone.utc) - document.created_at.replace(tzinfo=timezone.utc)).total_seconds()
            if document.status == "processing" and age < settings.INDEX_TIMEOUT_SECONDS + 15:
                raise RecoveryConflict("Wait for document processing to finish")
            path = checked_path(root, document.storage_key, "uploads")
            staged = f"deleted-files/{operation_id}/{index}.deleted"
            if checked_path(root, staged, f"deleted-files/{operation_id}").exists():
                raise RecoveryConflict("Staging destination already exists")
            entries.append({"document_id": document.id, "original": document.storage_key,
                            "staged": staged, "existed": path.exists(),
                            **(fingerprint(path) if path.exists() else {"size": None, "sha256": None})})
        manifest = {"version": 1, "files": entries, "session_ids": sorted(row.id for row in sessions)}
        exclusive_documents(db, root, entries, prepared=True)
        db.add(Operation(id=operation_id, workspace_id=workspace.id, owner_id=str(owner_id),
                         state="PREPARED", root=str(root), manifest=manifest, manifest_sha256=digest(manifest)))
        db.commit()  # No file has moved, even if the acknowledgement is lost.


def check_records(db, operation):
    workspace = db.get(Workspace, operation.workspace_id)
    documents = db.scalars(select(Document).where(Document.workspace_id == operation.workspace_id)).all()
    if operation.state == "PREPARED":
        expected = {item["document_id"]: item["original"] for item in operation.manifest["files"]}
        if (workspace is None or str(workspace.user_id) != operation.owner_id
                or {row.id: row.storage_key for row in documents} != expected):
            raise RecoveryConflict("Prepared operation disagrees with database records")
    else:
        ids = [item["document_id"] for item in operation.manifest["files"]]
        if (workspace is not None or documents
                or db.scalar(select(Document.id).where(Document.id.in_(ids)).limit(1))
                or db.scalar(select(ChatSession.id).where(ChatSession.id.in_(operation.manifest["session_ids"])).limit(1))):
            raise RecoveryConflict("Committed operation disagrees with database records")
    exclusive_documents(db, operation.root, operation.manifest["files"], operation.state == "PREPARED")


def delete_transaction(bind, operation_id, root):
    with Session(bind) as db:
        operation = locked_operation(db, operation_id)
        if operation.state != "PREPARED":
            raise RecoveryConflict("Deletion is no longer prepared")
        check_records(db, operation)
        apply_files(operation, root, "stage")
        delete_sessions(db, operation.manifest["session_ids"])
        ids = [item["document_id"] for item in operation.manifest["files"]]
        db.query(Chunk).filter(Chunk.document_id.in_(ids)).delete(synchronize_session=False)
        db.query(Document).filter(Document.id.in_(ids)).delete(synchronize_session=False)
        db.delete(db.get(Workspace, operation.workspace_id))
        operation.state = "COMMITTED"
        db.commit()  # Business deletion and outcome marker are inseparable.


def record_error(bind, operation_id, error):
    try:
        with Session(bind) as db:
            operation = locked_operation(db, operation_id)
            operation.last_error = f"{type(error).__name__}: {getattr(error, 'detail', str(error))}"[:2000]
            db.commit()
    except Exception:
        logger.exception("Could not persist recovery error for operation %s", operation_id)


def recover_locked(bind, operation_id, root):
    try:
        with Session(bind) as db:
            operation = locked_operation(db, operation_id)
            if operation.state in ("RESTORED", "CLEANED"):
                return operation.state
            check_records(db, operation)
            apply_files(operation, root, "restore" if operation.state == "PREPARED" else "clean")
            operation.state = "RESTORED" if operation.state == "PREPARED" else "CLEANED"
            state = operation.state
            operation.last_error = None
            db.commit()
            return state
    except Exception as error:
        record_error(bind, operation_id, error)
        raise


def recover_operation(bind, operation_id, root):
    """Trusted local entry point; no HTTP authorization bypass or background worker."""
    require_engine(bind)
    with operation_gates():
        return recover_locked(bind, operation_id, checked_root(root))


def delete_workspace(bind, workspace_id, owner_id, root):
    require_engine(bind)
    with operation_gates():
        operation_id = str(uuid.uuid4())
        prepare(bind, workspace_id, owner_id, root, operation_id)
        try:
            delete_transaction(bind, operation_id, root)
        except Exception:
            # Resolve on a new Session after the failed Session has closed. A lost
            # COMMIT reply may resolve to COMMITTED, in which case only clean up.
            if recover_locked(bind, operation_id, root) == "CLEANED":
                return operation_id
            raise
        recover_locked(bind, operation_id, root)
        return operation_id
