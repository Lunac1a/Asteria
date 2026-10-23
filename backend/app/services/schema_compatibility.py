"""Read-only mapped-column contract; not a general migration/version framework."""
import hashlib
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable, CreateIndex

from app.db.base import Base
from app.models import users, user_llm_settings, chat_sessions, messages, knowledge  # noqa: F401
from app.models import chat_turn, session_profile, learning, workspace_delete_operation  # noqa: F401


def model_revision():
    dialect = postgresql.dialect()
    ddl = []
    for table in Base.metadata.sorted_tables:
        ddl.append(str(CreateTable(table).compile(dialect=dialect)))
        ddl.extend(sorted(str(CreateIndex(i).compile(dialect=dialect)) for i in table.indexes))
    return hashlib.sha256('\n'.join(ddl).encode()).hexdigest()


def validate_schema(bind):
    inspector = inspect(bind)
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            raise RuntimeError(f"Missing schema table: {table.name}")
        columns = {c['name']: c for c in inspector.get_columns(table.name)}
        for column in table.columns:
            actual = columns.get(column.name)
            if actual is None:
                raise RuntimeError(f"Missing schema column: {table.name}.{column.name}")
            expected_type = column.type.compile(dialect=bind.dialect)
            actual_type = actual['type'].compile(dialect=bind.dialect)
            if expected_type != actual_type or column.nullable != actual['nullable']:
                raise RuntimeError(f"Incompatible schema column: {table.name}.{column.name}")


def validate_startup(bind, root):
    if (Path(root) / '.restore-incomplete').exists():
        raise RuntimeError('Restore has not completed; startup refused')
    validate_schema(bind)
