"""Request-time fail-closed gate; never recover an in-flight operation here."""
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.workspace_delete_operation import PENDING, WorkspaceDeleteOperation


def guard_recovery(request: Request, db: Session = Depends(get_db)):
    if request.url.path in {"/api/health", "/api/login", "/api/register", "/api/me"}:
        return
    workspace_delete = (request.method == "DELETE"
                        and request.url.path.startswith("/api/workspaces/")
                        and len(request.url.path.split("/")) == 4)
    if settings.WORKSPACE_DELETE_MAINTENANCE_MODE and not workspace_delete:
        raise HTTPException(503, "Resource operations are disabled during deletion maintenance")
    try:
        pending = db.scalar(select(WorkspaceDeleteOperation.id).where(
            WorkspaceDeleteOperation.state.in_(PENDING)).limit(1))
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "Recovery state unavailable; resource operations are disabled") from None
    if pending:
        raise HTTPException(503, "Pending workspace deletion requires manual recovery")
