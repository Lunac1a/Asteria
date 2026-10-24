"""Fixed representative RAG call: 24 interleaved original/optimized, profiled/unprofiled invocations."""
import hashlib
import json
import os
import subprocess
import threading
import time
import uuid
from provider_ab_hooks import ORDER, trace_for

import httpx
from sqlalchemy import text
from rag_c1_analysis import summarize, distribution
from rag_c1_resources import sample


def rows_hashes(engine):
    from app.db.base import Base
    with engine.connect() as db:
        return {t.name: hashlib.sha256('\n'.join(sorted(db.scalars(
            text(f'SELECT row_to_json(t)::text FROM "{t.name}" t')))).encode()).hexdigest()
            for t in Base.metadata.sorted_tables if t.name not in {
                'chat_sessions', 'messages', 'chat_turns', 'message_evidence'}}


def snapshot(proc, out, name):
    path = out / ('api-' + name + '.json')
    proc.stdin.write(name + '\n')
    proc.stdin.flush()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if path.exists():
            try:
                return json.loads(path.read_text())
            except json.JSONDecodeError:
                pass
        threading.Event().wait(.02)
    raise RuntimeError('Snapshot deadline')


def verify_response(record, question, documents):
    body = json.loads(record['response_body'])
    record['oracle_ok'] = question['fact'] in body['answer'] and any(
        s['document_id'] == documents[question['document']] and question['fact'] in s['content']
        for s in body['sources'])
    record['response_hash'] = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def request_attempt(client, payload, trace, question, documents, clock=time.monotonic,
                    defer_verification=False):
    record = dict(start=clock(), trace_id=trace)
    response = None
    try:
        # Non-streaming HTTPX returns after reading the response body.
        response = client.post('/api/chat', headers={'x-asteria-trace': trace}, json=payload)
    except httpx.TimeoutException:
        record['error'] = 'timeout'
    except httpx.TransportError:
        record['error'] = 'transport'
    finally:
        record['end'] = clock()
        record['elapsed_ms'] = (record['end']-record['start'])*1000
    if response is not None:
        record.update(status=response.status_code, trace_id=response.headers.get('x-asteria-trace'))
        if response.status_code == 200:
            record['response_body'] = response.content.decode('utf-8')
            if not defer_verification:
                verify_response(record, question, documents)
    return record


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
    last_guard = start
    low_memory_since = None
    failed = None
    ledger = (out / 'requests.jsonl').open('w', encoding='utf-8')
    try:
        for index, sid in enumerate(sessions[:len(ORDER)]):
            now = time.monotonic()
            if now >= deadline:
                break
            if now - last_guard >= 5:
                usage = memory()
                low = usage['available'] < max(2 * 2**30, usage['total'] * .1)
                low_memory_since = (low_memory_since or now) if low else None
                assert not low_memory_since or now-low_memory_since < 10, 'Low-memory stop'
                assert usage['disk_free'] >= 5 * 2**30, 'Disk-space stop'
                assert sum(p.stat().st_size for p in out.rglob('*') if p.is_file()) < 5 * 2**30
                assert now-budget_start < 1500, 'Wall-clock budget stop'
                last_guard = now
            q = data['queries'][0]
            rid, trace = str(uuid.uuid4()), trace_for(out, index)
            record = request_attempt(client, dict(message=q['question'], workspace_id=wid,
                session_id=sid, request_id=rid, answer_mode='document',
                session_type='questioning', learning_mode='direct'), trace, q, documents,
                defer_verification=True)
            record.update(request_id=rid, session_id=sid, query_id=q['id'], index=index, profile_mode=ORDER[index])
            rows.append(record)
            ledger.write(json.dumps(record)+'\n')
            # C1 stops conservatively on any unexpected outcome, never retries.
            if record.get('status') != 200 or any(not c['valid'] for c in calls[original_calls:]):
                failed = 'Unexpected HTTP outcome or oracle failure'
                boundary = min(deadline, record['end'])
                break
            if index == len(ORDER)-1:
                boundary = record['end']
        end = time.monotonic()
    finally:
        ledger.close()
        stop.set()
        thread.join(6)
        save(out / 'external-resources.json', resources)
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
                row['database_ok'] = len(turns) <= 1 and count in (2, 4)
        assert db.scalar(text('SELECT count(*) FROM chat_turns')) == 60 + len(rows)
        assert db.scalar(text('SELECT count(*) FROM messages')) == 720 + 120 + 2*len(rows)
        assert db.scalar(text('SELECT count(*) FROM message_evidence')) == 60 + len(rows)
        for sid in sessions[len(rows):]:
            assert db.scalar(text('SELECT count(*) FROM messages WHERE session_id=:s'), {'s': sid}) == 2
    save(out / 'verified-requests.json', rows)
    assert stable == rows_hashes(engine), 'Unexpected state changes outside Chat tables'
    assert all(row['database_ok'] for row in rows), 'Persisted state invariant failed'
    assert all(row.get('oracle_ok') for row in rows), 'Response oracle failed'
    assert not failed, failed
    assert len(rows) == len(ORDER), 'Incomplete fixed call schedule'
    assert len(calls)-original_calls == len(rows), 'Unexpected Provider count'
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
    assert {c['model_tag'][5:] for c in calls[original_calls:]} == ids, 'Provider attribution mismatch'
    assert sum(a['http_request_id'] in ids for a in after['admissions']) == len(rows)
    for e in records:
        assert abs(e['stage_covered_ms'] + e['unexplained_ms'] - e['server_elapsed_ms']) < .05
        assert e.get('execution_source') == 'fresh_generation' and e.get('singleflight_role') == 'leader'
        assert len(e['provider_calls']) == 1
    summary = summarize(rows, start, boundary)
    summary.update(drain_seconds=max(0, end-boundary), start=start, end=boundary,
        stop_reason='fixed_24_calls',
        provider_calls=len(calls)-original_calls, admitted=len(rows),
        server_ms=distribution([e['server_elapsed_ms'] for e in records]),
        unexplained_ms=distribution([e['unexplained_ms'] for e in records]),
        stages={stage: distribution([sum(s['duration_ms'] for s in e['stages'] if s['name'] == stage)
                                   for e in records]) for stage in {s['name'] for e in records for s in e['stages']}},
        writer_before=before['writer'], writer_after=after['writer'],
        correctness='all measured requests verified; stable non-Chat tables')
    save(out / 'measurement.json', summary)
    return summary
