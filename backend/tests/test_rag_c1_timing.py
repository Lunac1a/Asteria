"""Synthetic clock tests only: no API, database, Provider or performance load."""
from pathlib import Path
import sys
import unittest
import json
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).parent / 'experiments'))
from rag_c1_measure import request_attempt, verify_response  # noqa: E402
from rag_c1_analysis import summarize  # noqa: E402


class TimingTests(unittest.TestCase):
    def test_tcp_completion_waits_for_complete_body(self):
        headers_sent, release = threading.Event(), threading.Event()
        body = b'{"answer":"fact","sources":[{"document_id":"doc","content":"fact"}]}'

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.flush()
                headers_sent.set()
                if release.wait(5):
                    self.wfile.write(body)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}', trust_env=False,
                              timeout=5) as client, ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(request_attempt, client, {}, 'trace', {}, {},
                                     defer_verification=True)
                self.assertTrue(headers_sent.wait(3))
                self.assertFalse(future.done())
                released_at = time.monotonic()
                release.set()
                row = future.result(timeout=5)
                self.assertGreaterEqual(row['end'], released_at)
                self.assertEqual(row['response_body'].encode(), body)
                verify_response(row, {'fact': 'fact', 'document': 'name'}, {'name': 'doc'})
                self.assertTrue(row['oracle_ok'])
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join(3)

    def test_post_response_validation_does_not_move_http_completion_past_window(self):
        clock = [119.0]

        class Response:
            status_code = 200
            headers = {'x-asteria-trace': 'trace'}

            content = json.dumps(dict(answer='fact', sources=[dict(document_id='doc', content='fact')])).encode()

        class Client:
            def post(self, *args, **kwargs):
                clock[0] = 119.99  # Body fully received before the 120-second boundary.
                return Response()

        original = verify_response

        def slow_verify(*args):
            clock[0] += .05
            return original(*args)

        with patch('rag_c1_measure.verify_response', slow_verify):
            row = request_attempt(Client(), {}, 'trace', {'fact': 'fact', 'document': 'name'},
                                  {'name': 'doc'}, clock=lambda: clock[0])
        row['database_ok'] = True
        self.assertAlmostEqual(row['elapsed_ms'], 990)
        self.assertEqual(row['end'], 119.99)
        self.assertGreater(clock[0], 120)
        self.assertEqual(summarize([row], 0, 120)['goodput'], 1 / 120)

    def test_deferred_verification_cannot_delay_next_request(self):
        response = httpx.Response(200, json={'answer': 'fact', 'sources': [
            {'document_id': 'doc', 'content': 'fact'}]})
        calls = []

        class Client:
            def post(self, *args, **kwargs):
                calls.append(1)
                return response

        with patch('rag_c1_measure.verify_response', side_effect=AssertionError('must be offline')):
            rows = [request_attempt(Client(), {}, 'trace', {}, {}, defer_verification=True) for _ in range(2)]
        self.assertEqual(len(calls), 2)
        for row in rows:
            self.assertNotIn('oracle_ok', row)
            verify_response(row, {'fact': 'fact', 'document': 'name'}, {'name': 'doc'})
            self.assertTrue(row['oracle_ok'])

    def test_timeout_and_rejection_keep_their_outcomes(self):
        for status in [429, 503, 'timeout', 'transport']:
            clock = [1.0]

            class Client:
                def post(self, *args, **kwargs):
                    clock[0] = 2.0
                    if status == 'timeout':
                        raise httpx.ReadTimeout('synthetic')
                    if status == 'transport':
                        raise httpx.ConnectError('synthetic')
                    return httpx.Response(status, headers={'x-asteria-trace': 'trace'})

            row = request_attempt(Client(), {}, 'trace', {}, {}, clock=lambda: clock[0])
            self.assertEqual(row['elapsed_ms'], 1000)
            if isinstance(status, int):
                self.assertEqual(row['status'], status)
            else:
                self.assertEqual(row['error'], status)
            self.assertNotIn('oracle_ok', row)


if __name__ == '__main__':
    unittest.main()
