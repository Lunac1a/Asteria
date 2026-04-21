import uuid
from sqlalchemy import String, ForeignKey, DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

user = relationship(
    "User",
    back_populates="llm_setting"
)

class UserLLMSetting(Base):
    __tablename__ = "user_llm_settings"

    id: Mapped[str] = mapped_column(
        String,
        primary_key=True
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True
    )

    provider: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    api_key: Mapped[str] = mapped_column(
        String,
        nullable=False
    )

    model_name: Mapped[str] = mapped_column(
        String,
        nullable=False
    )

    base_url: Mapped[str] = mapped_column(
        String,
        nullable=False
    )

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )