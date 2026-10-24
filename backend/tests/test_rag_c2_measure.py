"""Synthetic dispatch integration: no service, database or load experiment."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch

import unittest

sys.path.insert(0, str(Path(__file__).parent / 'experiments'))
import rag_c2_measure as target  # noqa: E402
from rag_c1_analysis import summarize  # noqa: E402


def test_two_lanes_unique_sessions_cap_and_offline_boundary(self):
    barrier = threading.Barrier(2)
    seen = set()
    lock = threading.Lock()

    class Client:
        base_url, headers, cookies = 'http://unused', {}, {}

        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def attempt(client, payload, trace, *args, **kwargs):
        assert kwargs['defer_verification']
        with lock:
            first = threading.get_ident() not in seen
            seen.add(threading.get_ident())
        if first:
            barrier.wait(3)
        now = time.monotonic()
        return dict(start=now, end=now, elapsed_ms=0, status=429, trace_id=trace)

    def snapshot(proc, out, name):
        if name == 'after':
            raise RuntimeError('offline boundary reached')
        return {'worker': {'state': 'READY', 'restarts': 0}}

    with tempfile.TemporaryDirectory() as directory:
        out = Path(directory)
        with patch.object(target, 'snapshot', snapshot), patch.object(target, 'rows_hashes', return_value={}), \
                patch.object(target, 'request_attempt', attempt), patch.object(target.httpx, 'Client', Client), \
                patch.object(target, 'sample', side_effect=RuntimeError('synthetic unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'offline boundary'):
                target.measure(Client(), None, None, out,
                    {'queries': [{'question': 'synthetic', 'id': str(i)} for i in range(60)]},
                    list(range(300)), 'workspace', {}, [], 'unused', time.monotonic())
        rows = [json.loads(line) for line in (out / 'requests.jsonl').read_text().splitlines()]
        assert len(rows) == 300
        assert {r['lane'] for r in rows} == {0, 1}
        for key in ('request_id', 'session_id', 'trace_id', 'index'):
            assert len({r[key] for r in rows}) == 300
        assert all(r['status'] == 429 for r in rows)


def test_rejection_and_drain_success_do_not_inflate_goodput(self):
    rows = [dict(status=429, end=1, elapsed_ms=1),
            dict(status=200, end=3, elapsed_ms=2000, database_ok=True, oracle_ok=True)]
    result = summarize(rows, 0, 2)
    assert result['sent'] == 2 and result['successful'] == 1 and result['rejected'] == 1
    assert result['goodput'] == 0 and result['latency_all_http_ms']['n'] == 2


class C2Tests(unittest.TestCase):
    test_dispatch = test_two_lanes_unique_sessions_cap_and_offline_boundary
    test_accounting = test_rejection_and_drain_success_do_not_inflate_goodput
