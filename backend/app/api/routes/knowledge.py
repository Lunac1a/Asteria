import uuid
import threading
from functools import wraps
from pathlib import Path
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.deps import get_current_user_id
from app.db.session import get_db
from app.models.knowledge import Workspace, Document, Chunk
from app.services.knowledge_service import (
    data_root,
    model_id,
    owned_workspace,
    run_worker,
)

router = APIRouter()
document_slot = threading.BoundedSemaphore(1)


def document_operation(function):
    # Local MVP runs exactly one API worker. Serialize mutations and quotas.
    @wraps(function)
    def guarded(*args, **kwargs):
        if not document_slot.acquire(blocking=False):
            raise HTTPException(
                429,
                "A document operation is already running. Please try again shortly.",
            )
        try:
            return function(*args, **kwargs)
        finally:
            document_slot.release()

    return guarded


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class WorkspaceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    created_at: datetime


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    status: str
    error: str | None
    chunk_count: int
    size_bytes: int
    embedding_model: str
    created_at: datetime


@router.get("/learning/config")
def learning_config(user_id=Depends(get_current_user_id)):
    return {
        "embedding_backend": settings.EMBEDDING_BACKEND,
        "embedding_model": settings.EMBEDDING_MODEL,
        "llm_backend": settings.LLM_BACKEND,
        "max_upload_bytes": settings.MAX_UPLOAD_BYTES,
    }


@router.get("/workspaces", response_model=list[WorkspaceRead])
def workspaces(user_id=Depends(get_current_user_id), db: Session = Depends(get_db)):
    return (
        db.query(Workspace)
        .filter_by(user_id=user_id)
        .order_by(Workspace.created_at.desc())
        .limit(100)
        .all()
    )


@router.post("/workspaces", response_model=WorkspaceRead)
@document_operation
def create_workspace(
    payload: WorkspaceCreate,
    user_id=Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    name = payload.name.strip()
    if not name:
        raise HTTPException(422, "Workspace name cannot be blank")
    if db.query(Workspace).filter_by(user_id=user_id).count() >= 100:
        raise HTTPException(409, "Maximum 100 workspaces")
    workspace = Workspace(user_id=user_id, name=name)
    db.add(workspace)
    db.commit()
    db.refresh(workspace)
    return workspace


@router.get("/workspaces/{workspace_id}/documents", response_model=list[DocumentRead])
def documents(
    workspace_id: str,
    user_id=Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    owned_workspace(db, workspace_id, user_id)
    return (
        db.query(Document)
        .filter_by(workspace_id=workspace_id)
        .order_by(Document.created_at.desc())
        .all()
    )


def owned_document(db, workspace_id, document_id, user_id):
    owned_workspace(db, workspace_id, user_id)
    document = (
        db.query(Document).filter_by(id=document_id, workspace_id=workspace_id).first()
    )
    if not document:
        raise HTTPException(404, "Document not found")
    return document


def index_document(db, document):
    # The workspace cap also bounds the number of vectors loaded per query.
    existing = (
        db.query(Chunk)
        .join(Document, Chunk.document_id == Document.id)
        .filter(
            Document.workspace_id == document.workspace_id, Document.id != document.id
        )
        .count()
    )
    if existing + 400 > settings.MAX_WORKSPACE_CHUNKS:
        db.query(Chunk).filter_by(document_id=document.id).delete()
        document.chunk_count = 0
        document.status, document.error = (
            "failed",
            "Workspace index is full. Delete some documents.",
        )
        db.commit()
        return document
    try:
        path = str(data_root() / document.storage_key)
        db.commit()  # Release the read transaction while the bounded worker runs.
        output = run_worker(
            {"path": path},
            settings.INDEX_TIMEOUT_SECONDS,
        )
        db.query(Chunk).filter_by(document_id=document.id).delete()
        for ordinal, chunk in enumerate(output["chunks"]):
            db.add(Chunk(document_id=document.id, ordinal=ordinal, **chunk))
        document.status, document.error = "ready", None
        document.chunk_count = len(output["chunks"])
        document.embedding_model = model_id()
        db.commit()
    except HTTPException as exc:
        db.rollback()
        document.status, document.error, document.chunk_count = (
            "failed",
            str(exc.detail),
            0,
        )
        db.query(Chunk).filter_by(document_id=document.id).delete()
        db.commit()
    db.refresh(document)
    return document


@router.post("/workspaces/{workspace_id}/documents", response_model=DocumentRead)
@document_operation
def upload(
    workspace_id: str,
    file: UploadFile = File(...),
    user_id=Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    owned_workspace(db, workspace_id, user_id)
    if db.query(Document).filter_by(workspace_id=workspace_id).count() >= 20:
        raise HTTPException(409, "Maximum 20 documents per workspace")
    name = (file.filename or "document").replace("\\", "/").split("/")[-1][:240]
    extension = Path(name).suffix.lower()
    if extension not in {".pdf", ".md", ".txt"}:
        raise HTTPException(415, "Upload a text PDF, UTF-8 Markdown or TXT file")
    document_id = str(uuid.uuid4())
    storage_key = f"uploads/{document_id}{extension}"
    path = data_root() / storage_key
    path.parent.mkdir(exist_ok=True)
    size = 0
    try:
        with path.open("xb") as target:
            while block := file.file.read(64 * 1024):
                size += len(block)
                if size > settings.MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "Maximum file size is 10 MiB")
                target.write(block)
        if not size:
            raise HTTPException(422, "Empty file")
        document = Document(
            id=document_id,
            workspace_id=workspace_id,
            name=name,
            storage_key=storage_key,
            size_bytes=size,
            embedding_model=model_id(),
        )
        db.add(document)
        db.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        file.file.close()
    return index_document(db, document)


@router.post(
    "/workspaces/{workspace_id}/documents/{document_id}/reindex",
    response_model=DocumentRead,
)
@document_operation
def reindex(
    workspace_id: str,
    document_id: str,
    user_id=Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    document = owned_document(db, workspace_id, document_id, user_id)
    age = (
        datetime.now(timezone.utc) - document.created_at.replace(tzinfo=timezone.utc)
    ).total_seconds()
    if document.status == "processing" and age < settings.INDEX_TIMEOUT_SECONDS + 15:
        raise HTTPException(409, "Document is still processing")
    document.status, document.error = "processing", None
    db.commit()
    return index_document(db, document)


@router.get("/workspaces/{workspace_id}/documents/{document_id}/file")
def download(
    workspace_id: str,
    document_id: str,
    user_id=Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    document = owned_document(db, workspace_id, document_id, user_id)
    path = data_root() / document.storage_key
    if not path.is_file():
        raise HTTPException(404, "Local file is missing; upload it again")
    return FileResponse(
        path, filename=document.name, media_type="application/octet-stream"
    )


@router.delete("/workspaces/{workspace_id}/documents/{document_id}", status_code=204)
@document_operation
def delete_document(
    workspace_id: str,
    document_id: str,
    user_id=Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    document = owned_document(db, workspace_id, document_id, user_id)
    if document.status == "processing":
        age = (
            datetime.now(timezone.utc)
            - document.created_at.replace(tzinfo=timezone.utc)
        ).total_seconds()
        if age < settings.INDEX_TIMEOUT_SECONDS + 15:
            raise HTTPException(409, "Wait for processing to finish")
    path = data_root() / document.storage_key
    db.query(Chunk).filter_by(document_id=document.id).delete()
    db.delete(document)
    db.commit()
    path.unlink(missing_ok=True)
