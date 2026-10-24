"""Two independent closed-loop lanes, I/S0/C2 only; frozen C1 helpers retained."""
import hashlib
import json
import os
import subprocess
import threading
import time
import uuid

import httpx
from sqlalchemy import text
from rag_c1_analysis import summarize, distribution
from rag_c1_resources import sample


from rag_c1_measure import rows_hashes, snapshot, verify_response, request_attempt

def measure(client, engine, proc, out, data, sessions, wid, documents, calls, pg, budget_start):
    from rag_c1_preflight import memory, save
    before = snapshot(proc, out, 'before')
    assert before['worker']['state'] == 'READY' and before['worker']['restarts'] == 0
    stable = rows_hashes(engine)
    original_calls = len(calls)
    resources, rows = [], []
    stop = threading.Event()

    def sampler():
        iteration = 0
        while not stop.is_set():
            item = dict(at=time.monotonic())
            try:
                item['generator_mock'] = sample(os.getpid())
                item['system'] = memory()
                if iteration % 5 == 0:
                    stats = subprocess.run(['docker', 'stats', '--no-stream', '--format', '{{json .}}', pg],
                        capture_output=True, text=True, timeout=4)
                    item['postgres'] = json.loads(stats.stdout) if stats.returncode == 0 else {'unavailable': True}
            except Exception as error:
                item['error'] = type(error).__name__
            resources.append(item)
            iteration += 1
            stop.wait(1)

    thread = threading.Thread(target=sampler, daemon=True)
    thread.start()
    start = time.monotonic()
    deadline = start + 120
    boundary = deadline
    lock = threading.Lock()
    next_index = 0
    failed = None
    last_guard = start
    low_memory_since = None
    ledger = (out / 'requests.jsonl').open('w', encoding='utf-8')
    errors = []

    def lane(number):
        nonlocal next_index, boundary, failed, last_guard, low_memory_since
        try:
            with httpx.Client(base_url=client.base_url, headers=client.headers,
                              cookies=client.cookies, timeout=90, trust_env=False,
                              limits=httpx.Limits(max_connections=1, max_keepalive_connections=1)) as http:
                while True:
                    with lock:
                        now = time.monotonic()
                        if failed or next_index >= 300 or now >= deadline:
                            return
                        if now-last_guard >= 5:
                            usage = memory()
                            low = usage['available'] < max(2 * 2**30, usage['total'] * .1)
                            low_memory_since = (low_memory_since or now) if low else None
                            assert not low_memory_since or now-low_memory_since < 10, 'Low-memory stop'
                            assert usage['disk_free'] >= 5 * 2**30, 'Disk-space stop'
                            assert sum(p.stat().st_size for p in out.rglob('*') if p.is_file()) < 5 * 2**30
                            assert now-budget_start < 1500, 'Wall-clock budget stop'
                            last_guard = now
                        index = next_index
                        next_index += 1
                        if next_index == 300:
                            boundary = now
                    sid = sessions[index]
                    q = data['queries'][index % 60]
                    rid, trace = str(uuid.uuid4()), str(uuid.uuid4())
                    record = request_attempt(http, dict(message=q['question'], workspace_id=wid,
                        session_id=sid, request_id=rid, answer_mode='document',
                        session_type='questioning', learning_mode='direct'), trace, q, documents,
                        defer_verification=True)
                    record.update(request_id=rid, session_id=sid, query_id=q['id'], lane=number, index=index)
                    with lock:
                        rows.append(record)
                        ledger.write(json.dumps(record)+'\n')
                        # Conservative stop for unexpected outcomes; expected admission 429 remains visible.
                        if record.get('status') not in (200, 429):
                            failed = 'Unexpected outcome; no retry'
                            boundary = min(boundary, record['end'])
                        bucket = int((record['end']-start)//10)
                        if bucket >= 2:
                            windows = [[r for r in rows if start+(bucket-i-1)*10 <= r['start'] < start+(bucket-i)*10]
                                       for i in (0, 1)]
                            if all(len(w) >= 20 and sum(r.get('status') == 429 for r in w)/len(w) > .2 for w in windows):
                                failed = 'Overload stop: two consecutive rejection windows'
                                boundary = min(boundary, record['end'])
        except Exception as error:
            with lock:
                errors.append(type(error).__name__)
                failed = 'Lane guard or tool failure'
                boundary = min(boundary, time.monotonic())

    lanes = [threading.Thread(target=lane, args=(i,)) for i in range(2)]
    try:
        for worker in lanes:
            worker.start()
        for worker in lanes:
            worker.join(max(0, deadline+90-time.monotonic()))
        assert not any(worker.is_alive() for worker in lanes), 'Unknown in-flight operation'
        end = time.monotonic()
    finally:
        ledger.close()
        stop.set()
        thread.join(6)
        save(out / 'external-resources.json', resources)
        save(out / 'window.json', dict(start=start, end=boundary, drained_at=time.monotonic(),
             stop=failed or ('request_cap' if next_index == 300 else '120_seconds'), lane_errors=errors))
    if next_index == 300 and not failed:
        boundary = max(r['start'] for r in rows)
    rows.sort(key=lambda r: r['index'])
    successes = [r for r in rows if r.get('status') == 200]
    # SQL and writer flush occur exclusively after window/drain.
    after = snapshot(proc, out, 'after')
    for row in rows:
        if row.get('status') == 200:
            q = next(q for q in data['queries'] if q['id'] == row['query_id'])
            verify_response(row, q, documents)
    save(out / 'verified-requests.json', rows)
    with engine.connect() as db:
        for row in rows:
            turns = db.execute(text('SELECT session_id, question, response FROM chat_turns WHERE request_id=:r'),
                               {'r': row['request_id']}).mappings().all()
            row['database_outcome'] = 'committed' if turns else 'absent'
            count = db.scalar(text('SELECT count(*) FROM messages WHERE session_id=:s'), {'s': row['session_id']})
            if row.get('status') == 200:
                q = next(q for q in data['queries'] if q['id'] == row['query_id'])
                row['database_ok'] = (len(turns) == 1 and count == 4 and
                    turns[0]['session_id'] == row['session_id'] and turns[0]['question'] == q['question'] and
                    hashlib.sha256(json.dumps(turns[0]['response'], sort_keys=True).encode()).hexdigest() == row['response_hash'])
                sources = db.execute(text('SELECT e.sources FROM message_evidence e JOIN messages m ON m.id=e.message_id WHERE m.session_id=:s'),
                                     {'s': row['session_id']}).scalars().all()
                row['database_ok'] &= len(sources) == 1 and sources[0] == turns[0]['response']['sources']
            else:
                row['database_ok'] = not turns and count == 2 and db.scalar(text('SELECT count(*) FROM message_evidence e JOIN messages m ON m.id=e.message_id WHERE m.session_id=:s'), {'s': row['session_id']}) == 0
        assert db.scalar(text('SELECT count(*) FROM chat_turns')) == 60 + len(successes)
        assert db.scalar(text('SELECT count(*) FROM messages')) == 720 + 120 + 2*len(successes)
        assert db.scalar(text('SELECT count(*) FROM message_evidence')) == 60 + len(successes)
        for sid in sessions[len(rows):]:
            assert db.scalar(text('SELECT count(*) FROM messages WHERE session_id=:s'), {'s': sid}) == 2
    save(out / 'verified-requests.json', rows)
    assert stable == rows_hashes(engine), 'Unexpected state changes outside Chat tables'
    assert all(row['database_ok'] for row in rows), 'Persisted state invariant failed'
    assert all(row.get('oracle_ok') for row in successes), 'Response oracle failed'
    assert not failed, failed
    assert len(calls)-original_calls == len(successes), 'Unexpected Provider count'
    assert all(c['valid'] for c in calls[original_calls:]), 'Mock evidence failed'
    assert after['worker']['state'] == 'READY' and after['worker']['restarts'] == 0
    for lane in ['http', 'lifecycle']:
        for counter in ['dropped', 'failed']:
            assert after['writer'][lane][counter] == before['writer'][lane][counter], 'Telemetry loss'
    telemetry = []
    for path in (out / 'storage/runtime').glob('metrics.jsonl*'):
        telemetry += [json.loads(line) for line in path.read_text().splitlines()]
    traces = {r['trace_id'] for r in rows}
    records = [e for e in telemetry if e.get('trace_id') in traces and e.get('operation') == 'chat']
    save(out / 'measured-telemetry.json', records)
    assert len(records) == len(rows), 'HTTP telemetry incomplete'
    ids = {e['http_request_id'] for e in records}
    success_traces = {r['trace_id'] for r in successes}
    success_ids = {e['http_request_id'] for e in records if e['trace_id'] in success_traces}
    assert {c['model_tag'][5:] for c in calls[original_calls:]} == success_ids, 'Provider attribution mismatch'
    assert sum(a['http_request_id'] in ids for a in after['admissions']) == len(successes)
    for e in records:
        assert abs(e['stage_covered_ms'] + e['unexplained_ms'] - e['server_elapsed_ms']) < .05
        if e['trace_id'] not in success_traces:
            assert not e['provider_calls']
            continue
        assert e.get('execution_source') == 'fresh_generation' and e.get('singleflight_role') == 'leader'
        assert len(e['provider_calls']) == 1
    summary = summarize(rows, start, boundary)
    summary.update(drain_seconds=max(0, end-boundary), start=start, end=boundary,
        stop_reason='request_cap' if len(rows) == 300 else '120_seconds',
        provider_calls=len(calls)-original_calls, admitted=len(successes),
        server_ms=distribution([e['server_elapsed_ms'] for e in records]),
        unexplained_ms=distribution([e['unexplained_ms'] for e in records]),
        stages={stage: distribution([sum(s['duration_ms'] for s in e['stages'] if s['name'] == stage)
                                   for e in records if any(s['name'] == stage for s in e['stages'])]) for stage in {s['name'] for e in records for s in e['stages']}},
        writer_before=before['writer'], writer_after=after['writer'],
        correctness='all measured requests verified; stable non-Chat tables')
    summary['latency_by_status'] = {str(status): distribution([r['elapsed_ms'] for r in rows if r.get('status') == status]) for status in {r.get('status') for r in rows}}
    summary['outcomes'] = {str(status): sum(r.get('status') == status for r in rows) for status in {r.get('status') for r in rows}}
    save(out / 'measurement.json', summary)
    return summary
