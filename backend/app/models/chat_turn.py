from sqlalchemy import ForeignKey, JSON, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
import uuid
from app.db.base import Base


class ChatTurn(Base):
    __tablename__ = "chat_turns"
    request_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"))
    question: Mapped[str] = mapped_column(String)
    response: Mapped[dict] = mapped_column(JSON)
