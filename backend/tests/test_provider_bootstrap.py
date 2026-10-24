"""Standalone imports and unchanged HTTP translation; no network calls."""
import subprocess
import sys
import unittest
from pathlib import Path


class BootstrapTests(unittest.TestCase):
    def test_protocol_without_web_framework(self):
        code = '''
import sys
class BlockWeb:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'fastapi', 'starlette'}:
            raise AssertionError('Unexpected web-framework import')
sys.meta_path.insert(0, BlockWeb())
from app.services.llm.protocol import GenerationError, GenerationText, parse_object
assert GenerationError('provider_timeout').category == 'provider_timeout'
assert GenerationText('synthetic', {'x': 1}).metadata == {'x': 1}
assert parse_object('{"ok": true}') == {'ok': True}
'''
        subprocess.run([sys.executable, '-B', '-c', code],
                       cwd=Path(__file__).resolve().parents[1], check=True, timeout=10)

    def test_http_failure_translation(self):
        from fastapi import HTTPException
        from app.services.llm.protocol import GenerationError, failure
        for category in ['provider_timeout', 'provider_protocol', 'provider_connection',
                         'provider_rate_limit', 'provider_error', 'schema_validation']:
            for operation in ['chat', 'recap']:
                with self.subTest(category=category, operation=operation):
                    exc = failure(GenerationError(category), operation)
                    self.assertIsInstance(exc, HTTPException)
                    self.assertEqual(exc.status_code, 504 if category == 'provider_timeout' else 502)
                    self.assertEqual(exc.generation_category, category)
                    self.assertIsInstance(exc.detail, str)

    def test_ab_schedule_and_source_safety(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent/'experiments'))
        from provider_ab_hooks import ORDER, child_source, process_source
        backend = Path(__file__).resolve().parents[1]
        self.assertEqual(len(ORDER), 24)
        self.assertTrue(all(ORDER.count(mode) == 6 for mode in set(ORDER)))
        source = child_source((backend/'app/services/llm/provider.py').read_text(), backend)
        compile(source, '<ab-child>', 'exec')
        for retained in ['max_retries=0', 'follow_redirects=False', 'limit_memory()', 'APIResponse.parse']:
            self.assertIn(retained, source)
        source = process_source((backend/'app/services/llm/process.py').read_text())
        compile(source, '<ab-process>', 'exec')
        for retained in ['job.kill()', 'job.close()', '_quarantine.append', '1024 * 1024 + 1']:
            self.assertIn(retained, source)
