"""Additive local MVP tables; existing chat and user schemas stay compatible."""

import uuid
from datetime import datetime
from sqlalchemy import String, ForeignKey, DateTime, Integer, JSON, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID
from app.db.base import Base


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(240))
    storage_key: Mapped[str] = mapped_column(String)
    size_bytes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default="processing")
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding_model: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Chunk(Base):
    __tablename__ = "document_chunks"
    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(String)
    vector: Mapped[list] = mapped_column(JSON)


class SessionWorkspace(Base):
    __tablename__ = "session_workspaces"
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)


class MessageEvidence(Base):
    __tablename__ = "message_evidence"
    message_id: Mapped[str] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True
    )
    sources: Mapped[list] = mapped_column(JSON)
    ai_mode: Mapped[str] = mapped_column(String)
