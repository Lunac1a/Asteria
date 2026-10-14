"""Exercise provider bootstrap outside the backend import root, without network."""

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


class ProviderEntrypointTests(unittest.TestCase):
    def test_child_loads_shared_memory_guard_and_returns_mock_sdk_answer(self):
        provider = Path(__file__).resolve().parents[1] / "app/services/llm/provider.py"
        script = textwrap.dedent("""
            import io, json, runpy, sys
            from types import SimpleNamespace as NS
            from unittest.mock import patch
            # No PYTHONPATH or application cwd: the provider must bootstrap itself.
            runpy.run_path(sys.argv[1], run_name="provider_bootstrap")
            response = NS(usage=None, choices=[NS(finish_reason="stop",
                message=NS(content="synthetic child answer", refusal=None,
                           tool_calls=None, function_call=None))])
            sys.stdin = io.StringIO(json.dumps(dict(messages=[], api_key="synthetic",
                base_url="https://example.invalid/v1", model_name="synthetic")))
            with patch("app.services.knowledge.index_worker.limit_memory") as guard, \
                 patch("openai.OpenAI") as sdk:
                sdk.return_value.__enter__.return_value.chat.completions.create.return_value = response
                runpy.run_path(sys.argv[1], run_name="__main__")
                assert guard.call_count == 1
                assert sdk.return_value.__enter__.return_value.chat.completions.create.call_count == 1
        """)
        env = {
            k: v
            for k, v in os.environ.items()
            if k.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP"}
        }
        result = subprocess.run(
            [sys.executable, "-c", script, str(provider)],
            cwd=provider.parents[4],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            check=True,
        )
        self.assertEqual(json.loads(result.stdout)["answer"], "synthetic child answer")
