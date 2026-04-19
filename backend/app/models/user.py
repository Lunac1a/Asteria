from sqlalchemy import String, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime, timezone
import uuid

from app.db.base import Base

class User(Base):
    __tablename__ = "users"

    id : Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    email : Mapped[str] = mapped_column(
        String,
        unique=True,
        index=True,
        nullable=False
    )

    password_hash : Mapped[str] = mapped_column(
        String,
        nullable=False
    )

    created_at : Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc)
    )