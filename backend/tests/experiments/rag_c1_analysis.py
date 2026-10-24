"""Pure outcome/latency accounting for the approved C1 window."""
import math
import statistics


def distribution(values):
    values = sorted(values)
    if not values:
        return {'n': 0}
    result = dict(n=len(values), min=min(values), max=max(values), mean=statistics.mean(values),
                  descriptive_median=statistics.median(values))
    for percentile, threshold in [(50, 20), (95, 200), (99, 1000)]:
        if len(values) >= threshold:
            result[f'p{percentile}'] = values[math.ceil(percentile * len(values) / 100) - 1]
    return result


def summarize(rows, start, end):
    window = end - start
    successes = [r for r in rows if r.get('status') == 200 and r.get('database_ok') and r.get('oracle_ok')]
    return dict(window_seconds=window, offered=len(rows), sent=len(rows),
        completed_http=sum('status' in r for r in rows), successful=len(successes),
        rejected=sum(r.get('status') in (409, 429, 503) for r in rows),
        timeouts=sum(r.get('error') == 'timeout' for r in rows),
        transport_errors=sum(r.get('error') == 'transport' for r in rows),
        goodput=sum(r['end'] <= end for r in successes) / window,
        offered_rate=len(rows) / window,
        latency_all_http_ms=distribution([r['elapsed_ms'] for r in rows if 'status' in r]),
        latency_success_ms=distribution([r['elapsed_ms'] for r in successes]))
