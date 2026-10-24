"""Deterministic clock tests for fixed arrivals and bounded outstanding work."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent / 'experiments'))
from rag_arrival_scheduler import schedule  # noqa: E402
from rag_c1_analysis import summarize  # noqa: E402


class Clock:
    now = 0.0

    def read(self):
        return self.now

    def wait(self, seconds):
        self.now += seconds


class ArrivalTests(unittest.TestCase):
    def simulate(self, duration, seconds=120):
        clock = Clock()
        sends = []

        class Future:
            def __init__(self):
                self.end = clock.now+duration

            def done(self):
                return clock.now >= self.end

        def submit(index, lane, scheduled):
            sends.append((index, lane, scheduled, clock.now))
            return Future()

        result = schedule(submit, clock=clock.read, wait=clock.wait, seconds=seconds)
        return result, sends, clock

    def test_fixed_arrivals_independent_of_completion(self):
        result, sends, clock = self.simulate(1.8)
        self.assertEqual([s[3] for s in sends], list(range(120)))
        self.assertEqual({s[1] for s in sends}, {0, 1})
        self.assertEqual(result['end'], 120)
        self.assertEqual(clock.now, 120)
        self.assertTrue(all(p['outstanding_before'] < 2 for p in result['plans']))

    def test_full_slots_drop_without_queue_or_catchup(self):
        result, sends, _ = self.simulate(3, seconds=6)
        self.assertEqual([s[0] for s in sends], [0, 1, 3, 4])
        dropped = [p for p in result['plans'] if not p['sent']]
        self.assertEqual([p['index'] for p in dropped], [2, 5])
        self.assertTrue(all(p['reason'] == 'client_outstanding_limit' for p in dropped))
        self.assertEqual(len(sends)+len(dropped), 6)

    def test_ninety_second_work_cannot_create_unbounded_queue(self):
        result, sends, _ = self.simulate(90)
        self.assertEqual([s[0] for s in sends], [0, 1, 90, 91])
        self.assertEqual(sum(not p['sent'] for p in result['plans']), 116)

    def test_late_arrival_is_not_sent_as_a_burst(self):
        clock = Clock()
        sends = []

        def wait(seconds):
            clock.now += seconds+.2

        class Done:
            def done(self):
                return True

        def submit(index, lane, scheduled):
            sends.append(index)
            return Done()

        result = schedule(submit, clock=clock.read, wait=wait, seconds=4)
        self.assertEqual(sends, [0])
        self.assertTrue(all(p['reason'] == 'scheduler_late' for p in result['plans'][1:]))

    def test_guard_stops_future_dispatch(self):
        clock = Clock()
        result = schedule(lambda *args: self.fail('must not submit'), guard=lambda: 'unsafe',
                          clock=clock.read, wait=clock.wait)
        self.assertEqual(result['stopped'], 'unsafe')
        self.assertEqual(result['plans'][0]['reason'], 'safety_stop')
        self.assertEqual(len(result['futures']), 0)

    def test_drain_failure_rejection_and_not_sent_denominators(self):
        rows = [dict(status=200, database_ok=True, oracle_ok=True, end=119, elapsed_ms=1800),
                dict(status=200, database_ok=True, oracle_ok=True, end=121, elapsed_ms=1900),
                dict(status=429, end=4, elapsed_ms=10), dict(error='timeout', end=92)]
        summary = summarize(rows, 0, 120)
        self.assertEqual(summary['goodput'], 1/120)
        self.assertEqual(summary['successful'], 2)
        self.assertEqual(summary['rejected'], 1)
        self.assertEqual(summary['timeouts'], 1)
        self.assertEqual(summary['sent'], 4)
        # Offered must come from the arrival ledger, not the sent-only summary.
        offered, not_sent = 120, 116
        self.assertEqual(summary['sent']+not_sent, offered)
