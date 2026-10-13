"""Manual recovery, only after an operator verifies a single API process.

The confirmation setting is an operational attestation, not process discovery.
All indexing must use knowledge.document_slot; multiple API processes are unsupported.
"""

import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.routes.knowledge import document_slot
from app.core.config import settings
from app.core.deps import get_current_user_id
from app.db.session import get_db
from app.models.knowledge import Chunk, Document

router = APIRouter(prefix="/admin/document-recovery")
process_id = str(uuid.uuid4())
failure_message = "Previous indexing did not complete. Please reindex this document."


def recovery_admin(user_id=Depends(get_current_user_id)):
    if str(user_id) not in settings.RECOVERY_ADMIN_USER_IDS:
        raise HTTPException(403, "Recovery administrator access required")
    return user_id


class PreviewRequest(BaseModel):
    document_ids: list[str] = Field(min_length=1, max_length=20)


class RecoveryRequest(BaseModel):
    preview_token: str = Field(min_length=1, max_length=8192)
    confirm: bool = False


@router.post("/preview")
def preview(payload: PreviewRequest, user_id=Depends(recovery_admin), db: Session = Depends(get_db)):
    if not document_slot.acquire(blocking=False):
        raise HTTPException(409, "A document operation is running; try again after it finishes")
    try:
        db.rollback()  # End authentication's read transaction; discard cached entities.
        db.expire_all()
        ids = list(dict.fromkeys(payload.document_ids))
        rows = []
        for identifier in ids:
            document = db.get(Document, identifier)
            rows.append({
                "document_id": identifier,
                "status": document.status if document else "missing",
                "chunk_count": db.query(Chunk).filter_by(document_id=identifier).count() if document else 0,
            })
        token = jwt.encode({
            "sub": str(user_id), "purpose": "document-recovery", "process": process_id,
            "document_ids": ids, "exp": int(time.time()) + 300,
        }, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
        return {"documents": rows, "preview_token": token,
                "execution_enabled": settings.RECOVERY_SINGLE_PROCESS_CONFIRMED}
    finally:
        try:
            db.rollback()
        finally:
            document_slot.release()


@router.post("/execute")
def execute(payload: RecoveryRequest, user_id=Depends(recovery_admin), db: Session = Depends(get_db)):
    if not settings.RECOVERY_SINGLE_PROCESS_CONFIRMED:
        raise HTTPException(409, "Recovery disabled: operator must verify only one API process is running")
    if not payload.confirm:
        raise HTTPException(422, "Explicit recovery confirmation required")
    try:
        claims = jwt.decode(payload.preview_token, settings.JWT_SECRET_KEY,
                            algorithms=[settings.JWT_ALGORITHM])
        if (claims.get("sub") != str(user_id) or claims.get("purpose") != "document-recovery"
                or claims.get("process") != process_id or "exp" not in claims):
            raise ValueError("Invalid recovery preview")
        ids = claims["document_ids"]
        if not isinstance(ids, list) or not 1 <= len(ids) <= 20 or not all(isinstance(i, str) for i in ids):
            raise ValueError("Invalid documents")
    except (JWTError, ValueError, KeyError, TypeError):
        raise HTTPException(400, "Invalid or expired recovery preview; preview again")
    if not document_slot.acquire(blocking=False):
        raise HTTPException(409, "A document operation is running; no documents recovered")
    try:
        db.rollback()
        db.expire_all()
        recovered, skipped = [], []
        for identifier in ids:
            document = db.get(Document, identifier)
            if document is None or document.status != "processing":
                skipped.append(identifier)
                continue
            document.status = "failed"
            document.error = failure_message
            document.chunk_count = db.query(Chunk).filter_by(document_id=identifier).count()
            recovered.append(identifier)
        db.commit()
        return {"recovered": recovered, "skipped": skipped}
    finally:
        try:
            db.rollback()
        finally:
            document_slot.release()
