"""Additive learning state. Recap drafts are separate from lifecycle transitions."""
from datetime import datetime, timezone
from sqlalchemy import String, Text, Integer, JSON, DateTime, ForeignKey, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


def now():
    return datetime.now(timezone.utc)


class LearningContext(Base):
    __tablename__ = "learning_contexts"
    __table_args__ = (CheckConstraint("status IN ('active','finished')"),)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("chat_sessions.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String, default="active")
    cycle: Mapped[int] = mapped_column(Integer, default=1)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    focus: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    next_step: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    update_warning: Mapped[bool] = mapped_column(default=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LearningRecap(Base):
    __tablename__ = "learning_recaps"
    __table_args__ = (UniqueConstraint("session_id", "cycle", name="one_recap_per_finish"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True)
    cycle: Mapped[int] = mapped_column(Integer)
    # Nullable content records an explicit finish without recap, filled only on request.
    content: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    snapshot: Mapped[dict] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RecapDraft(Base):
    __tablename__ = "learning_recap_drafts"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True)
    cycle: Mapped[int] = mapped_column(Integer)
    context_revision: Mapped[int] = mapped_column(Integer)
    target_recap: Mapped[str | None] = mapped_column(String, nullable=True)
    content: Mapped[dict] = mapped_column(JSON)
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
