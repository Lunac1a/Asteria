"""Recovery records deliberately have no foreign keys to deletable resources."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, JSON, String, event, func, inspect, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

PENDING = ("PREPARED", "COMMITTED")


class WorkspaceDeleteOperation(Base):
    __tablename__ = "workspace_delete_operations"
    __table_args__ = (
        CheckConstraint("state IN ('PREPARED','COMMITTED','RESTORED','CLEANED')"),
        Index("uq_workspace_delete_pending", "workspace_id", unique=True,
              postgresql_where=text("state IN ('PREPARED','COMMITTED')"),
              sqlite_where=text("state IN ('PREPARED','COMMITTED')")),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String, nullable=False)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False, default="PREPARED")
    root: Mapped[str] = mapped_column(String, nullable=False)
    manifest: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)


@event.listens_for(WorkspaceDeleteOperation, "before_update")
def immutable_plan(mapper, connection, operation):
    attributes = inspect(operation).attrs
    for field in ("id", "workspace_id", "owner_id", "root", "manifest", "manifest_sha256"):
        if attributes[field].history.has_changes():
            raise ValueError("Persisted recovery plans are immutable")
    history = attributes.state.history
    if history.has_changes():
        allowed = {("PREPARED", "COMMITTED"), ("PREPARED", "RESTORED"), ("COMMITTED", "CLEANED")}
        if not history.deleted or (history.deleted[0], operation.state) not in allowed:
            raise ValueError("Invalid recovery transition")
