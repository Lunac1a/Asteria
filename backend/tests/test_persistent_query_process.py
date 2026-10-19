"""Real Windows Job/IPC deadlines with tiny controlled child processes, no model."""
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import test_auth  # noqa: F401 -- synthetic settings
from fastapi import HTTPException
from app.services.knowledge.persistent_query import PersistentQueryWorker


@unittest.skipUnless(os.name == "nt", "Windows Job Objects required")
class PersistentQueryProcessTests(unittest.TestCase):
    def run_child_case(self, script, startup_failure=False):
        from app.services.knowledge import windows_job as native
        handles = []
        original = native.Job.launch
        with tempfile.TemporaryDirectory(prefix="worker-deadline-",
                dir=Path(__file__).resolve().parents[2] / ".local") as folder:
            child = Path(folder) / "controlled_child.py"
            child.write_text(script, encoding="utf-8")
            worker = PersistentQueryWorker("synthetic", folder, threading.BoundedSemaphore(1),
                                           startup_timeout=.1 if startup_failure else 5)
            def launch(job, command, env):
                value = original(job, [sys._base_executable, "-B", str(child)], env)
                handles.append(native.process_handle(value[1]))
                return value
            try:
                with patch.object(native.Job, "launch", launch):
                    if startup_failure:
                        start = time.monotonic()
                        with self.assertRaises(TimeoutError):
                            worker.start()
                    else:
                        worker.start()
                        start = time.monotonic()
                        with self.assertRaises(HTTPException) as error:
                            worker.embed("synthetic query", .1)
                        self.assertEqual(error.exception.status_code, 503)
                    self.assertLess(time.monotonic()-start, 6)
                    self.assertEqual(worker.state, "FAILED")
                    self.assertFalse(worker.gate_held)
                    self.assertIsNone(worker.job)
                    self.assertFalse(worker.io_thread.is_alive())
                    self.assertTrue(all(native.exit_status(h, 1000)["exited"] for h in handles))
            finally:
                worker.close()
                for handle in handles:
                    native.close(handle)

    def test_startup_ready_deadline_kills_owned_process(self):
        self.run_child_case("import threading\nthreading.Event().wait(30)\n", startup_failure=True)

    def test_task_deadline_kills_unresponsive_worker(self):
        self.run_child_case(
            'import os, json, sys, threading\n'
            'print(json.dumps(dict(type="ready", pid=os.getpid(), model="synthetic")), flush=True)\n'
            'sys.stdin.readline()\nthreading.Event().wait(30)\n')

    def test_mismatched_task_id_kills_worker(self):
        self.run_child_case(
            'import os, json, sys, threading\n'
            'print(json.dumps(dict(type="ready", pid=os.getpid(), model="synthetic")), flush=True)\n'
            'sys.stdin.readline()\n'
            'print(json.dumps(dict(type="result", task_id="wrong", vectors=[[1.0]])), flush=True)\n'
            'threading.Event().wait(30)\n')
