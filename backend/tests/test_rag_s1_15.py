"""Deterministic scheduler validation; no real benchmark or database."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent / 'experiments'))
from rag_s1_15_scheduler import schedule, overload_stop  # noqa: E402


class Clock:
    now = 0.0

    def read(self):
        return self.now

    def wait(self, seconds):
        self.now += seconds


class S115Tests(unittest.TestCase):
    def simulate(self, duration, seconds=120):
        clock, sends = Clock(), []

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

    def test_exact_180_arrivals_without_accumulated_interval_drift(self):
        result, sends, clock = self.simulate(1.8)
        self.assertEqual(len(sends), 180)
        self.assertEqual([x[2] for x in sends], [i/1.5 for i in range(180)])
        self.assertEqual([x[3] for x in sends], [i/1.5 for i in range(180)])
        self.assertEqual(clock.now, 120)
        self.assertEqual(result['end'], 120)
        self.assertLess(sends[-1][2], 120)

    def test_four_busy_slots_drop_not_queue(self):
        result, sends, _ = self.simulate(90)
        self.assertEqual([x[0] for x in sends], [0, 1, 2, 3, 135, 136, 137, 138])
        self.assertEqual({x[1] for x in sends}, {0, 1, 2, 3})
        dropped = [p for p in result['plans'] if not p['sent']]
        self.assertEqual(len(dropped), 172)
        self.assertTrue(all(p['outstanding_before'] == 4 for p in dropped))
        self.assertTrue(all(p['reason'] == 'client_outstanding_limit' for p in dropped))

    def test_late_and_safety_stop_remain_explicit(self):
        clock = Clock()
        def submit(*args):
            self.fail('unsafe dispatch')
        result = schedule(submit, guard=lambda *args: 'unsafe', clock=clock.read, wait=clock.wait)
        self.assertEqual(result['stopped'], 'unsafe')
        self.assertEqual(result['plans'][0]['reason'], 'safety_stop')

    def test_overload_threshold_is_not_lowered_for_15_per_window(self):
        times = [i/1.5 for i in range(31)]
        rows = [dict(scheduled=t, status=429) for t in times]
        self.assertIsNone(overload_stop(rows, times, 20))
        times = [i/2 for i in range(41)]
        rows = [dict(scheduled=t, status=429) for t in times]
        self.assertIsNotNone(overload_stop(rows, times, 20))
        for fraction in (0.2, 0.25):
            rows = [dict(scheduled=t, status=429 if i % 20 < int(20*fraction) else 200)
                    for i,t in enumerate(times)]
            self.assertEqual(overload_stop(rows, times, 20) is not None, fraction > .2)

    def test_delayed_clock_does_not_burst_replay(self):
        clock, sent = Clock(), []
        class Done:
            def done(self):
                return True
        def submit(index, lane, at):
            sent.append(index)
            return Done()
        def wait(seconds):
            clock.now += seconds+.2
        result = schedule(submit, clock=clock.read, wait=wait, seconds=2)
        self.assertEqual(sent, [0])
        self.assertTrue(all(p['reason'] == 'scheduler_late' for p in result['plans'][1:]))
