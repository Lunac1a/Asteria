"""Additive session identity; legacy sessions deliberately have no profile."""
from sqlalchemy import String, Text, ForeignKey, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class SessionProfile(Base):
    __tablename__ = "session_profiles"
    __table_args__ = (CheckConstraint("session_type IN ('questioning', 'learning')", name="valid_session_type"),)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("chat_sessions.id", ondelete="CASCADE"), primary_key=True)
    session_type: Mapped[str] = mapped_column(String(20), nullable=False)
    learning_goal: Mapped[str | None] = mapped_column(Text, nullable=True)
