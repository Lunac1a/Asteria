"""Fixed arrival rate I/S0 or S1; 1 per second, maximum two outstanding."""
import hashlib
import json
import os
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from rag_arrival_scheduler import schedule

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
    lock = threading.Lock()
    last_guard = time.monotonic()
    low_memory_since = None
    failed = None
    ledger = (out / 'requests.jsonl').open('w', encoding='utf-8')
    clients = [httpx.Client(base_url=client.base_url, headers=client.headers,
                           cookies=client.cookies, timeout=90, trust_env=False,
                           limits=httpx.Limits(max_connections=1, max_keepalive_connections=1))
               for _ in range(2)]
    executor = ThreadPoolExecutor(max_workers=2)
    dispatched = []

    def attempt(index, lane, scheduled):
        q = data['queries'][index % 60]
        sid = sessions[index]
        rid, trace = str(uuid.uuid4()), str(uuid.uuid4())
        row = request_attempt(clients[lane], dict(message=q['question'], workspace_id=wid,
            session_id=sid, request_id=rid, answer_mode='document',
            session_type='questioning', learning_mode='direct'), trace, q, documents,
            defer_verification=True)
        row.update(request_id=rid, session_id=sid, query_id=q['id'], lane=lane,
                   index=index, scheduled=scheduled, dispatch_lag_ms=(row['start']-scheduled)*1000)
        with lock:
            rows.append(row)
            ledger.write(json.dumps(row)+'\n')
        return row

    def submit(index, lane, scheduled):
        future = executor.submit(attempt, index, lane, scheduled)
        dispatched.append(future)
        return future

    def guard():
        nonlocal last_guard, low_memory_since
        now = time.monotonic()
        with lock:
            if any(r.get('status') not in (200, 429) for r in rows):
                return 'Unexpected HTTP outcome; stop without retry'
        if now-last_guard >= 5:
            usage = memory()
            low = usage['available'] < max(2 * 2**30, usage['total'] * .1)
            low_memory_since = (low_memory_since or now) if low else None
            if low_memory_since and now-low_memory_since >= 10:
                return 'Low-memory stop'
            if usage['disk_free'] < 5 * 2**30:
                return 'Disk-space stop'
            if sum(p.stat().st_size for p in out.rglob('*') if p.is_file()) >= 5 * 2**30:
                return 'Evidence-size stop'
            if now-budget_start >= 1500:
                return 'Per-repetition wall-clock stop'
            last_guard = now
        return None

    plan = None
    try:
        plan = schedule(submit, guard)
        start, boundary = plan['start'], plan['end']
        failed = plan['stopped']
        for future in dispatched:
            future.result(timeout=max(.001, boundary+90-time.monotonic()))
        end = time.monotonic()
    finally:
        # HTTP timeout is unchanged at 90 seconds; drain all submitted work before resource teardown.
        executor.shutdown(wait=True)
        for http in clients:
            http.close()
        ledger.close()
        stop.set()
        thread.join(6)
        save(out / 'external-resources.json', resources)
        if plan:
            save(out / 'arrival-plan.json', {k:v for k,v in plan.items() if k != 'futures'})
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
        for sid in set(sessions)-{r['session_id'] for r in rows}:
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
    admitted_ids = {a['http_request_id'] for a in after['admissions']} & ids
    assert success_ids <= admitted_ids
    by_trace = {e['trace_id']: e for e in records}
    for row in rows:
        event = by_trace[row['trace_id']]
        row['admitted'] = event['http_request_id'] in admitted_ids
        if row.get('status') == 429:
            row['rejection_boundary'] = ('chat_admission' if not row['admitted'] else
                'material_acquisition' if any(s['name'] == 'material_acquisition' and s['outcome'] == 'failure' for s in event['stages']) else 'unknown_after_admission')
    save(out / 'verified-requests.json', rows)
    for e in records:
        assert abs(e['stage_covered_ms'] + e['unexplained_ms'] - e['server_elapsed_ms']) < .05
        if e['trace_id'] not in success_traces:
            assert not e['provider_calls']
            continue
        assert e.get('execution_source') == 'fresh_generation' and e.get('singleflight_role') == 'leader'
        assert len(e['provider_calls']) == 1
    summary = summarize(rows, start, boundary)
    summary.update(drain_seconds=max(0, end-boundary), start=start, end=boundary,
        stop_reason='120_seconds',
        provider_calls=len(calls)-original_calls, admitted=len(admitted_ids),
        server_ms=distribution([e['server_elapsed_ms'] for e in records]),
        unexplained_ms=distribution([e['unexplained_ms'] for e in records]),
        stages={stage: distribution([sum(s['duration_ms'] for s in e['stages'] if s['name'] == stage)
                                   for e in records if any(s['name'] == stage for s in e['stages'])]) for stage in {s['name'] for e in records for s in e['stages']}},
        writer_before=before['writer'], writer_after=after['writer'],
        correctness='all measured requests verified; stable non-Chat tables')
    summary['latency_by_status'] = {str(status): distribution([r['elapsed_ms'] for r in rows if r.get('status') == status]) for status in {r.get('status') for r in rows}}
    summary['outcomes'] = {str(status): sum(r.get('status') == status for r in rows) for status in {r.get('status') for r in rows}}
    summary.update(scheduled=len(plan['plans']), offered=len(plan['plans']),
        not_sent=sum(not p['sent'] for p in plan['plans']), offered_rate=len(plan['plans'])/(boundary-start),
        sent_rate=len(rows)/(boundary-start), planned_arrivals=120,
        dispatch_lag_ms=distribution([r['dispatch_lag_ms'] for r in rows]),
        schedule_latency_http_ms=distribution([(r['end']-r['scheduled'])*1000 for r in rows if 'status' in r]))
    assert summary['sent']+summary['not_sent'] == summary['scheduled'] == 120
    assert {r['index'] for r in rows} == {p['index'] for p in plan['plans'] if p['sent']}
    assert not any(p.get('reason') == 'scheduler_late' for p in plan['plans']), 'Scheduler missed arrival; measurement limited'
    assert max((r['dispatch_lag_ms'] for r in rows), default=0) <= 100, 'Dispatch lag limit'
    save(out / 'measurement.json', summary)
    return summary
