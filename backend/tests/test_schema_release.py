"""Release preflight on the existing explicitly isolated PostgreSQL fixture."""
import unittest
from sqlalchemy import text
import test_workspace_delete_recovery as protocol
from app.services.schema_compatibility import validate_startup


class ReleaseSchemaTests(unittest.TestCase):
    setUp = protocol.WorkspaceDeleteRecoveryTests.setUp

    def test_schema_and_incomplete_restore_are_gated(self):
        if not self.postgres:
            self.skipTest('Requires explicitly isolated PostgreSQL')
        validate_startup(self.bind, self.root)
        with self.bind.begin() as db:
            db.execute(text('ALTER TABLE users RENAME COLUMN email TO incompatible_email'))
        with self.assertRaisesRegex(RuntimeError, 'Missing schema column'):
            validate_startup(self.bind, self.root)
        with self.bind.begin() as db:
            db.execute(text('ALTER TABLE users RENAME COLUMN incompatible_email TO email'))
        (self.root / '.restore-incomplete').touch()
        with self.assertRaisesRegex(RuntimeError, 'not completed'):
            validate_startup(self.bind, self.root)
