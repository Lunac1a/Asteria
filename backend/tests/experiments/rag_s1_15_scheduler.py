"""Fixed 1.5/s open-loop schedule, at most four outstanding, no queued arrivals."""
import time
import threading


def overload_stop(rows, sent_times, now):
    """Original I rule: >20% 429 in two full 10s windows, >=20 offered each.

    At fixed 1.5/s each full window offers 15: the sample threshold is deliberately
    NOT reduced to force this rule to trigger. Other safety guards remain active.
    """
    if not sent_times:
        return None
    start = sent_times[0]
    bucket = int((now-start)//10)
    if bucket < 2:
        return None
    windows = []
    for i in (0, 1):
        low, high = start+(bucket-i-1)*10, start+(bucket-i)*10
        offered = sum(low <= at < high for at in sent_times)
        rejected = sum(low <= r['scheduled'] < high and r.get('status') == 429 for r in rows)
        windows.append(offered >= 20 and rejected/offered > .2)
    return 'Overload stop: two consecutive rejection windows' if all(windows) else None


def schedule(submit, guard=lambda *args: None, *, clock=time.monotonic,
             wait=threading.Event().wait, seconds=120):
    start = clock()
    slots = [None] * 4
    plans, futures = [], []
    stopped = None
    for index in range(int(seconds * 1.5)):
        scheduled = start + index / 1.5
        remaining = scheduled-clock()
        if remaining > 0:
            wait(remaining)
        now = clock()
        stopped = stopped or guard(start, index, now)
        item = dict(index=index, scheduled=scheduled, decision_at=now,
                    dispatch_lag_ms=max(0, now-scheduled)*1000)
        available = [i for i, future in enumerate(slots) if future is None or future.done()]
        item['outstanding_before'] = 4-len(available)
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
                planned_arrivals=int(seconds * 1.5))
