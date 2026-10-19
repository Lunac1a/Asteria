"""Real bounded Windows IPC plus deterministic ownership failure injection."""
import ctypes
import os
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from app.services.llm import process


@unittest.skipUnless(os.name == "nt", "Windows Job Objects")
class ProviderProcessTests(unittest.TestCase):
    def test_normal_and_pipe_eof(self):
        result = process.run_provider(
            [sys._base_executable, "-c", "import sys; print(sys.stdin.read())"],
            input="synthetic", env=dict(os.environ), timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "synthetic")
        self.assertFalse(process._quarantine)

    def test_blocked_stdin_is_bounded_and_cleaned(self):
        start = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            process.run_provider(
                [sys._base_executable, "-c", "import threading; threading.Event().wait(20)"],
                input="x" * 1000000, env=dict(os.environ), timeout=.2)
        self.assertLess(time.monotonic() - start, 6)
        self.assertFalse(process._quarantine)

    def test_job_attribute_failure_never_creates_process(self):
        from app.services.knowledge import windows_job as native
        original = native.bind
        created = []

        def bind(name, *args, **kwargs):
            if name == "UpdateProcThreadAttribute":
                update = original(name, *args, **kwargs)
                def fail(*args):
                    if args[2] == 0x2000D:  # Reject JOB_LIST specifically, after HANDLE_LIST.
                        ctypes.set_last_error(50)
                        return 0
                    return update(*args)
                return fail
            if name == "CreateProcessW":
                created.append(name)
            return original(name, *args, **kwargs)

        with patch.object(native, "bind", bind), self.assertRaises(OSError):
            process.run_provider([sys.executable, "-c", "pass"], input="", env={}, timeout=1)
        self.assertEqual(created, [])
        self.assertFalse(process._quarantine)

    def test_unconfirmed_cleanup_latches_execution_closed(self):
        from app.services.knowledge import windows_job as native

        class UncertainJob:
            def launch(self, *args):
                raise OSError("synthetic creation failure")

            def pids(self):
                return []

            def kill(self):
                pass

            def accounting(self):
                return {"active_processes": 1}

        try:
            with patch.object(native, "Job", UncertainJob), patch.object(process, "CLEANUP_SECONDS", .03):
                with self.assertRaisesRegex(OSError, "cleanup unconfirmed"):
                    process.run_provider([], input="", env={}, timeout=1)
                self.assertEqual(len(process._quarantine), 1)
                with self.assertRaisesRegex(OSError, "restart required"):
                    process.run_provider([], input="", env={}, timeout=1)
        finally:
            process._quarantine.clear()  # Synthetic record only; no native resource exists.

    def test_unsupported_platform_fails_closed(self):
        with patch.object(process.os, "name", "posix"), self.assertRaisesRegex(OSError, "requires Windows"):
            process.run_provider([], input="", env={}, timeout=1)
