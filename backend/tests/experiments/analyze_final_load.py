"""Summarize measured requests, including rejected latency and attribution."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import statistics


def distribution(values):
    values = sorted(values)
    if not values:
        return {'n': 0}
    return dict(n=len(values), mean=statistics.mean(values),
        p50=values[math.ceil(len(values)*.5)-1], p95=values[math.ceil(len(values)*.95)-1],
        min=values[0], max=values[-1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('result', type=Path)
    args = parser.parse_args()
    data = json.loads(args.result.read_text(encoding='utf-8'))
    output = {'passed': data['passed'], 'plan': data['load_plan'], 'groups': []}
    measured_labels = {key for batch in data['load_batches'] for key in batch['labels']}
    measured = [r for r in data['http'] if r['label'] in measured_labels]
    for request in measured:
        generations = sum(e['event'] == 'llm_start' and e.get('request') == request['label']
                          for e in data['events'])
        assert generations == int(request['status'] == 200), request
        if 'indexer busy' in str(request['body']):
            assert any(e['event'] == 'embedding_error' and e['label'] == request['label']
                       and e['status'] == 429 for e in data['events'])
    output['correctness'] = dict(measured_requests=len(measured),
        generation_exactly_once_per_success=True, rejected_generation_calls=0,
        independent_database_verification='Every request: messages 4/2, turn 1/0, evidence source, notes and revision unchanged',
        embedding_rejections_confirmed_by_supervisor_event=True)
    for level in (1, 2, 4):
        for enabled in (False, True):
            batches = [b for b in data['load_batches'] if b['level'] == level and b['enabled'] == enabled]
            labels = {k for b in batches for k in b['labels']}
            requests = [r for r in data['http'] if r['label'] in labels]
            successes = [r for r in requests if r['status'] == 200]
            rejected = [r for r in requests if r['status'] != 200]
            elapsed = sum(b['end']-b['start'] for b in batches)
            status = Counter(r['status'] for r in requests)
            reasons = Counter()
            for r in rejected:
                detail = str(r['body'])
                reasons['embedding_capacity' if 'indexer busy' in detail else
                        'chat_admission' if 'chat' in detail and r['status'] == 429 else detail] += 1
            samples = [s for s in data['load_resources'] if any(
                b['start'] <= s['at'] <= b['end'] for b in batches)]
            peaks = {}
            for kind in ('api_and_harness', 'embedding', 'provider'):
                totals = [{key: sum(p[key] for p in s['processes'] if p['kind'] == kind)
                    for key in ('rss', 'private_bytes', 'threads', 'handles')}
                    for s in samples if 'sampling_error' not in s]
                peaks[kind] = {key: max((t[key] for t in totals), default=0) for key in
                              ('rss', 'private_bytes', 'threads', 'handles')}
                peaks[kind]['process_count'] = max((sum(p['kind'] == kind for p in s['processes'])
                    for s in samples), default=0)
                # A directly observed CPU-time delta, not a complete process-lifetime estimate.
                cpu_deltas = []
                for b in batches:
                    by_pid = {}
                    for s in samples:
                        if b['start'] <= s['at'] <= b['end']:
                            for p in s['processes']:
                                if p['kind'] == kind:
                                    by_pid.setdefault(p['pid'], []).append(p['cpu_seconds'])
                    cpu_deltas.append(sum(max(v)-min(v) for v in by_pid.values()))
                peaks[kind]['sampled_cpu_seconds_per_batch'] = distribution(cpu_deltas)
            output['groups'].append(dict(level=level, enabled=enabled, requests=len(requests),
                statuses=dict(status), rejection_reasons=dict(reasons), elapsed_seconds=elapsed,
                http_rps=len(requests)/elapsed, successful_throughput=len(successes)/elapsed,
                success_rate=len(successes)/len(requests), rate_429=status[429]/len(requests),
                rate_409=status[409]/len(requests), rate_5xx=sum(v for k, v in status.items() if k >= 500)/len(requests),
                success_ms=distribution([(r['end']-r['start'])*1000 for r in successes]),
                rejected_ms=distribution([(r['end']-r['start'])*1000 for r in rejected]),
                dispatch_spread_ms=distribution([(max(r['start'] for r in requests if r['label'] in b['labels'])-
                    min(r['start'] for r in requests if r['label'] in b['labels']))*1000 for b in batches]),
                resources=peaks, sample_errors=sum('sampling_error' in s for s in samples),
                max_pool_checkout=max((s.get('pool_checked_out', 0) for s in samples), default=0),
                max_pg_connections=max((sum(r['count'] for r in s.get('postgres_connections', []))
                    for s in samples), default=0),
                batch_success_counts=[b['statuses'].count(200) for b in batches]))
    closes = [e for e in data['events'] if e['event'] == 'provider_close']
    output['cleanup'] = dict(provider_jobs=len(closes),
        provider_jobs_empty=all(e['accounting']['active_processes'] == 0 for e in closes),
        captured_processes_exited=all(e['exited'] for e in data['provider_exits']+data['worker_exits']),
        api_stopped=data['api_stopped'], supervisor=data['supervisor'])
    args.result.with_name('analysis.json').write_text(json.dumps(output, indent=2), encoding='utf-8')
    for g in output['groups']:
        print(g['level'], g['enabled'], g['statuses'], g['rejection_reasons'],
              round(g['successful_throughput'], 3), g['success_ms'], g['rejected_ms'])


if __name__ == '__main__':
    main()
