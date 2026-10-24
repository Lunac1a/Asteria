"""Bounded open-loop schedule. Never waits for a response to generate arrivals."""
import time
import threading


def schedule(submit, guard=lambda: None, *, clock=time.monotonic,
             wait=threading.Event().wait, seconds=120):
    start = clock()
    slots = [None, None]
    plans, futures = [], []
    stopped = None
    for index in range(seconds):
        scheduled = start + index
        remaining = scheduled-clock()
        if remaining > 0:
            wait(remaining)
        now = clock()
        stopped = stopped or guard()
        item = dict(index=index, scheduled=scheduled, decision_at=now,
                    dispatch_lag_ms=max(0, now-scheduled)*1000)
        available = [i for i, future in enumerate(slots) if future is None or future.done()]
        item['outstanding_before'] = 2-len(available)
        if stopped:
            item.update(sent=False, reason='safety_stop')
        elif now >= start+seconds or now-scheduled > .1:
            # Missed arrivals are dropped, never accumulated into a catch-up queue.
            item.update(sent=False, reason='scheduler_late')
        elif not available:
            item.update(sent=False, reason='client_outstanding_limit')
        else:
            lane = available[0]
            future = submit(index, lane, scheduled)
            slots[lane] = future
            futures.append(future)
            item.update(sent=True, lane=lane)
        plans.append(item)
        if stopped:
            break
    boundary = start+seconds if not stopped else clock()
    if clock() < boundary:
        wait(boundary-clock())
    return dict(start=start, end=boundary, plans=plans, futures=futures, stopped=stopped,
                planned_arrivals=seconds)
