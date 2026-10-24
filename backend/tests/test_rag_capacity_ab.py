"""Snapshot selection and unchanged subprocess contract; no load generation."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent/'experiments'))
import capacity_ab_run as runner  # noqa: E402


class CapacityABTests(unittest.TestCase):
    def test_child_preserves_execution_and_only_changes_protocol_location(self):
        source = (runner.fixture.BACKEND/'app/services/llm/provider.py').read_text()
        first = runner.child_source(source, runner.fixture.BACKEND, Path('original.py'))
        second = runner.child_source(source, runner.fixture.BACKEND, Path('optimized.py'))
        self.assertEqual(first.replace('original.py', 'optimized.py'), second)
        compile(first, '<original>', 'exec')
        for retained in ['limit_memory()', 'max_retries=0', 'follow_redirects=False', 'run_provider(']:
            self.assertIn(retained, first)

    def test_selection_preserves_input_environment_and_timeout(self):
        import test_auth  # noqa: F401 -- isolated settings before application imports
        from app.services.llm import provider
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            content = b'# synthetic snapshot; not executed by this unit test\n'
            for name in ['original', 'optimized']:
                (root/f'protocol-{name}.py').write_bytes(content)
            (root/'freeze.json').write_text(json.dumps(dict(limits=dict(order=['A1','B1']),
                protocols={name:hashlib.sha256(content).hexdigest() for name in ['original','optimized']})))
            for name, expected in [('A1','original'),('B1','optimized')]:
                out = root/name
                out.mkdir()
                with patch.object(provider, 'run_provider', return_value='synthetic') as original:
                    runner.install(out)
                    result = provider.run_provider(['python.exe','provider.py'], input='synthetic', env={'x':'y'}, timeout=45)
                    self.assertEqual(result, 'synthetic')
                    self.assertEqual(original.call_args.kwargs, dict(input='synthetic',env={'x':'y'},timeout=45))
                    self.assertEqual(original.call_args.args[0][0], 'python.exe')
                    identity = json.loads((out/'provider-source/identity.json').read_text())
                    self.assertEqual(identity['version'], expected)
