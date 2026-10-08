"""Explicit additive schema setup, restricted to loopback PostgreSQL."""
from sqlalchemy.engine import make_url
from app.core.config import settings
from app.db.base import Base
from app.db.session import engine
from app.models import users, user_llm_settings, chat_sessions, messages, knowledge, chat_turn  # noqa: F401


if __name__ == "__main__":
    url = make_url(settings.DATABASE_URL)
    if url.host not in {"127.0.0.1", "localhost", "::1"} or not url.drivername.startswith("postgresql"):
        raise SystemExit("Schema setup is restricted to local PostgreSQL. No changes made.")
    Base.metadata.create_all(engine)
    print("Local schema ready (additive tables only).")
