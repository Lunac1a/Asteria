"""Deterministic shared-storage acceptance; synthetic HTTP and controlled workers."""
import concurrent.futures
from datetime import datetime, timezone
import io
import json
import logging
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from contextlib import redirect_stderr

import test_auth  # noqa: F401
from test_query_supervisor import ControlledWorker
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.core.config import settings
from app.services import telemetry_writer as telemetry
from app.services.runtime_metrics import RuntimeMetrics
from app.services.knowledge import lifecycle_events
from app.services.knowledge.query_supervisor import QueryWorkerSupervisor


class WriterTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = str(self.root/'runtime/metrics.jsonl')
        self.enterContext(patch.object(settings, "LOCAL_DATA_DIR", str(self.root)))
        self.enterContext(patch.object(settings, "RUNTIME_METRICS_ENABLED", True))

    def writer(self, **kwargs):
        writer = telemetry.TelemetryWriter(**kwargs)
        self.addCleanup(lambda: self.assertTrue(writer.close(3)))
        return writer

    def test_original_blocker_http_and_recovery_complete_before_disk_release(self):
        entered, release, second = threading.Event(), threading.Event(), threading.Event()
        original = telemetry._write
        def held(path, line):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test guard")
            original(path, line)
        writer = self.writer(sink=held)
        self.enterContext(patch.object(telemetry, "writer", writer))
        app = FastAPI()
        app.add_middleware(RuntimeMetrics)
        @app.get('/api/first')
        async def first():
            return {"ok": True}
        @app.get('/api/second')
        async def other():
            second.set()
            return {"ok": True}
        s = QueryWorkerSupervisor("unused", ".", threading.BoundedSemaphore(1),
                                  worker_factory=ControlledWorker, backoff_seconds=.01)
        try:
            lifecycle_events.emit(dict(event_type="failure_detected", reason_code="unknown",
                                       timestamp_utc=datetime.now(timezone.utc).isoformat()))
            self.assertTrue(entered.wait(2))
            with TestClient(app) as client, concurrent.futures.ThreadPoolExecutor(2) as pool:
                a, b = pool.submit(client.get, '/api/first'), pool.submit(client.get, '/api/second')
                self.assertEqual(a.result(2).json(), {"ok": True})
                self.assertEqual(b.result(2).status_code, 200)
                self.assertTrue(second.is_set())
                s.start()
                with s.condition:
                    s.worker.alive = False
                    s.condition.notify_all()
                    self.assertTrue(s.condition.wait_for(lambda: s.restarts == 1 and s.state == "READY", 2))
                s.close()
                self.assertFalse(release.is_set())
        finally:
            release.set()
            s.close()
        self.assertTrue(writer.flush(3))
        rows = [json.loads(line) for line in Path(self.path).read_text().splitlines()]
        self.assertEqual(sum(r['kind'] == 'server_request' for r in rows), 2)
        self.assertIn('restart_completed', [r.get('event_type') for r in rows])
        print('WRITER_BLOCKER_AFTER', json.dumps(dict(http_completed_before_release=True,
              second_endpoint_entered=True, recovery_completed_before_release=True,
              valid_records=len(rows), counters=writer.snapshot())))

    def test_independent_capacity_priority_fairness_and_drop_counters(self):
        entered, release = threading.Event(), threading.Event()
        written = []
        def held(path, line):
            entered.set()
            release.wait(3)
            written.append(json.loads(line))
        writer = self.writer(http_capacity=3, lifecycle_capacity=6, sink=held)
        try:
            self.assertTrue(writer.submit({'kind':'server_request', 'id':'active'}, self.path))
            self.assertTrue(entered.wait(1))
            for i in range(3):
                self.assertTrue(writer.submit({'kind':'server_request', 'id':f'h{i}'}, self.path))
            self.assertFalse(writer.submit({'kind':'server_request'}, self.path))
            for i in range(6):
                self.assertTrue(writer.submit({'kind':'worker_lifecycle', 'id':f'l{i}'}, self.path))
            self.assertFalse(writer.submit({'kind':'worker_lifecycle'}, self.path))
            snap = writer.snapshot()
            self.assertEqual((snap['http']['queued'], snap['lifecycle']['queued']), (3,6))
            self.assertEqual((snap['http']['dropped'], snap['lifecycle']['dropped']), (1,1))
        finally:
            release.set()
        self.assertTrue(writer.flush(3))
        self.assertEqual([r['id'] for r in written], ['active','l0','l1','l2','l3','h0','l4','l5','h1','h2'])
        self.assertEqual(writer.snapshot()['lifecycle']['written'], 6)

    def test_real_handler_failure_counted_without_stderr_and_writer_survives(self):
        original = logging.FileHandler.emit
        count = 0
        def fail_once(handler, record):
            nonlocal count
            count += 1
            if count == 1:
                raise OSError('SECRET_EXCEPTION')
            return original(handler, record)
        writer = self.writer()
        err = io.StringIO()
        with patch.object(logging.FileHandler, 'emit', fail_once), redirect_stderr(err):
            writer.submit({'kind':'server_request','id':1}, self.path)
            writer.submit({'kind':'server_request','id':2}, self.path)
            self.assertTrue(writer.flush(3))
        self.assertEqual(err.getvalue(), '')
        self.assertEqual(writer.snapshot()['http']['failed'], 1)
        self.assertEqual(writer.snapshot()['http']['written'], 1)
        self.assertTrue(writer.thread.is_alive())
        self.assertEqual(json.loads(Path(self.path).read_text())['id'], 2)

    def test_concurrent_mixed_records_and_rotation_no_duplicates(self):
        handler = telemetry._Handler
        def small(*args, **kwargs):
            kwargs.update(maxBytes=1100, backupCount=60)
            return handler(*args, **kwargs)
        writer = self.writer()
        with patch.object(telemetry, '_Handler', small):
            def produce(group):
                for i in range(30):
                    self.assertTrue(writer.submit({'kind':'worker_lifecycle' if group%2 else 'server_request',
                        'id':f'{group}-{i}', 'version':'fixture'}, self.path))
            with concurrent.futures.ThreadPoolExecutor(4) as pool:
                list(pool.map(produce, range(4)))
            self.assertTrue(writer.flush(5))
        files = list(Path(self.path).parent.glob('metrics.jsonl*'))
        rows = [json.loads(line) for f in files for line in f.read_text().splitlines()]
        self.assertGreater(len(files), 1)
        self.assertEqual(len(rows), 120)
        self.assertEqual(len({r['id'] for r in rows}), 120)
        self.assertEqual({r['kind'] for r in rows}, {'server_request','worker_lifecycle'})

    def test_shutdown_is_bounded_and_rejects_new_items(self):
        entered, release = threading.Event(), threading.Event()
        def sink(path, line):
            entered.set()
            release.wait(3)
        writer = self.writer(sink=sink)
        try:
            writer.submit({'kind':'server_request'}, self.path)
            self.assertTrue(entered.wait(1))
            self.assertFalse(writer.flush(0))
            self.assertFalse(writer.close(0))
            self.assertFalse(writer.submit({'kind':'server_request'}, self.path))
            self.assertEqual(writer.snapshot()['http']['dropped'], 1)
        finally:
            release.set()
        self.assertTrue(writer.close(3))

    def test_freezes_payload_destination_and_counts_serialization_failure(self):
        writer = self.writer()
        row = {'kind':'server_request', 'value':[1]}
        writer.submit(row, self.path)
        row['value'].append(2)
        self.assertFalse(writer.submit({'kind':'server_request','value':object()}, self.path))
        self.assertTrue(writer.flush(3))
        self.assertEqual(json.loads(Path(self.path).read_text())['value'], [1])
        self.assertEqual(writer.snapshot()['http']['dropped'], 1)
