"""Storage fixtures must accept noncanonical TEMP paths without weakening guards."""
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_deletion
import test_workspace_delete_recovery as protocol


class StorageFixturePathTests(unittest.TestCase):
    def check_fixture(self, fixture_class):
        temporary_directory = tempfile.TemporaryDirectory
        with temporary_directory(prefix="asteria-path-regression-") as directory:
            parent = Path(directory).resolve()
            (parent / "child").mkdir()

            @contextmanager
            def aliased_temp(*args, **kwargs):
                kwargs["dir"] = parent
                with temporary_directory(*args, **kwargs) as created:
                    alias = parent / "child" / ".." / Path(created).name
                    self.assertNotEqual(alias, alias.resolve())
                    # The production guard must still reject the alias itself.
                    with self.assertRaises(protocol.recovery.RecoveryConflict):
                        protocol.recovery.checked_root(alias)
                    yield str(alias)

            case = fixture_class()
            try:
                # This fixture regression always uses its isolated SQLite setup.
                with patch.dict(os.environ, {"ASTERIA_RECOVERY_TEST_URL": ""}), \
                        patch.object(tempfile, "TemporaryDirectory", aliased_temp):
                    case.setUp()
                self.assertEqual(case.root, case.root.resolve())
                self.assertEqual(protocol.recovery.checked_root(case.root), case.root)
            finally:
                case.doCleanups()

    def test_deletion_fixture_canonicalizes_temp_path(self):
        self.check_fixture(test_deletion.DeletionTests)

    def test_recovery_fixture_canonicalizes_temp_path(self):
        self.check_fixture(protocol.WorkspaceDeleteRecoveryTests)
