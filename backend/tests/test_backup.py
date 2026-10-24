"""Offline integrity/target guards; PostgreSQL roundtrip is separately exercised."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_auth  # noqa: F401 - synthetic settings before application imports
from app.cli.backup import backup, digest, safe_file, verify, restore, enum_predicate
from app.services.schema_compatibility import model_revision, validate_startup


class BackupGuardsTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        # Hosted Windows runners may expose TEMP through a junction/short path.
        # Valid fixtures use its canonical path; production redirect guards stay intact.
        self.root = Path(self.temp).resolve()
        self.bundle = self.root / 'bundle'
        self.bundle.mkdir()
        self.dump = self.bundle / 'database.dump'
        self.dump.write_bytes(b'synthetic dump for integrity checks only')
        self.file = self.bundle / 'files/uploads/doc.txt'
        self.file.parent.mkdir(parents=True)
        self.file.write_bytes(b'synthetic document')
        self.manifest = dict(format=1, model_revision=model_revision(),
                             source=dict(system_id='1', database='source', server_version='18.6'),
                             database=dict(bytes=self.dump.stat().st_size, sha256=digest(self.dump)),
                             files={'uploads/doc.txt': dict(bytes=self.file.stat().st_size, sha256=digest(self.file))})
        self.save()

    def save(self):
        (self.bundle / 'manifest.json').write_text(json.dumps(self.manifest))

    def test_integrity(self):
        self.assertEqual(verify(self.bundle)['format'], 1)

    def test_redirected_bundle_is_rejected(self):
        with patch.object(Path, 'resolve', return_value=self.root / 'elsewhere'):
            with self.assertRaisesRegex(ValueError, 'Redirected backup directory'):
                verify(self.bundle)

    def test_missing_component(self):
        self.file.unlink()
        with self.assertRaises(ValueError):
            verify(self.bundle)

    def test_corrupted_dump(self):
        self.dump.write_bytes(b'corruption')
        with self.assertRaises(ValueError):
            verify(self.bundle)

    def test_schema_revision_mismatch(self):
        self.manifest['model_revision'] = 'unknown'
        self.save()
        with self.assertRaises(ValueError):
            verify(self.bundle)

    def test_path_traversal_and_secrets_rejected(self):
        for value in ('../.env', '/uploads/a', 'uploads/../a', 'C:/a', 'models/a', '.env'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                safe_file(self.root, value)

    def test_existing_destination_rejected_before_database_access(self):
        with self.assertRaisesRegex(ValueError, 'new directory'):
            restore(None, '', 'target', '', self.root, self.bundle, {})

    def test_original_database_rejected(self):
        with self.assertRaisesRegex(ValueError, 'original database'):
            restore(None, '', 'source', '', self.root / 'new', self.bundle, {'system_id': '1'})

    def test_incomplete_restore_blocks_startup(self):
        (self.root / '.restore-incomplete').touch()
        with self.assertRaisesRegex(RuntimeError, 'not completed'):
            validate_startup(None, self.root)

    def test_incomplete_restore_cannot_be_backed_up(self):
        (self.root / '.restore-incomplete').touch()
        with self.assertRaisesRegex(ValueError, 'incomplete restore'):
            backup(None, '', '', '', self.root, self.root / 'unused', {})

    def test_enum_dump_cast_normalization_preserves_values(self):
        before = "state::text = ANY (ARRAY['PREPARED'::character varying, 'COMMITTED'::character varying]::text[])"
        after = "((state)::text = ANY (ARRAY[('PREPARED'::character varying)::text, ('COMMITTED'::character varying)::text]))"
        self.assertEqual(enum_predicate(before), enum_predicate(after))
        self.assertNotEqual(enum_predicate(before), enum_predicate(after.replace('COMMITTED', 'CLEANED')))
        other = "amount::integer > 0"
        self.assertEqual(enum_predicate(other), other)
