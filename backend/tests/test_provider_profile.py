"""Profile transformation must compile, retain isolation, and emit numeric fields."""
import ast
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent/'experiments'))
from provider_profile_hooks import child_source, process_source, mark, ORDER  # noqa: E402


class ProfileTests(unittest.TestCase):
    def test_child_preserves_protocol_and_limits(self):
        backend = Path(__file__).parents[1]
        source = (backend/'app/services/llm/provider.py').read_text()
        output = child_source(source, backend)
        ast.parse(output)
        for text in ['max_retries=0', 'follow_redirects=False', 'limit_memory()',
                     'request_provider(**_payload)', 'except APITimeoutError:',
                     'sdk_timeout = min(30.0, max(0.5, timeout_seconds - 2))']:
            self.assertIn(text, output)
        self.assertEqual(ORDER.count('profile'), 6)
        self.assertEqual(ORDER.count('control'), 6)

    def test_parent_retains_shared_quarantine_and_cleanup(self):
        backend = Path(__file__).parents[1]
        source = (backend/'app/services/llm/process.py').read_text()
        output = process_source(source)
        ast.parse(output)
        self.assertNotIn('_quarantine =', output)
        for text in ['job.kill()', 'job.close()', 'thread.join(', '_quarantine.append(',
                     'CLEANUP_SECONDS', 'stdout.read(1024 * 1024 + 1)']:
            self.assertIn(text, output)

    def test_numeric_allowlisted_marks_only(self):
        events = []
        mark(events, 'sample')
        self.assertEqual(set(events[0]), {'name','wall_ns','cpu_thread_ns','thread'})
        self.assertTrue(all(isinstance(v, int) for k,v in events[0].items() if k != 'name'))
