"""Opt-in D1 real TCP/PG18/Windows Provider preflight. Never runs measured load.

Creates only a labeled disposable PostgreSQL container and a new evidence directory.
No production configuration is read. A failed preflight must not enter measurement.
"""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid

from rag_c1_dataset import dataset, digest, verify_index_identity

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / 'backend'
IMAGE = 'postgres@sha256:77f585114c32fbca283dc835b0596f4e52b51b4c6662d7810b2f4084f60a1873'


def save(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')


def docker(*args):
    result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=35)
    if result.returncode:
        raise RuntimeError(f'Docker {args[0]} failed (exit {result.returncode})')
    return result.stdout.strip()


def memory():
    class Status(ctypes.Structure):
        _fields_ = [('length', wintypes.DWORD), ('load', wintypes.DWORD)] + [
            (key, ctypes.c_ulonglong) for key in ['total', 'available', 'page_total',
                                                'page_available', 'virtual_total',
                                                'virtual_available', 'extended']]
    value = Status()
    value.length = ctypes.sizeof(value)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
        raise RuntimeError('Memory accounting unavailable')
    return dict(total=value.total, available=value.available,
                disk_free=shutil.disk_usage(ROOT).free)


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def api(out):
    # Environment supplied by parent contains exclusively synthetic app settings.
    sys.path.insert(0, str(BACKEND))
    import uvicorn
    from app.main import create_app
    from app.db.base import Base
    from app.db.session import engine
    from app.services.telemetry_writer import writer
    from app.services import runtime_metrics
    from app.services.llm import completion
    from app.api.routes import chat
    from app.core.config import settings
    from app.services.knowledge import windows_job as native
    from app.services.startup_recovery import storage_lease
    import rag_c1_resources

    Base.metadata.create_all(engine)
    app = create_app()
    app.state.benchmark_jobs = []
    sampling_stop, sampling_thread, samples = rag_c1_resources.start(app)
    job_records = []
    original_launch, original_close = native.Job.launch, native.Job.close

    def launch(job, command, env):
        value = original_launch(job, command, env)
        job.benchmark_kind = Path(command[-1]).name
        current = runtime_metrics.current.get()
        job.benchmark_http = current.get('http_request_id') if current else None
        app.state.benchmark_jobs.append(job)
        return value

    def close(job):
        if job.handle and hasattr(job, 'benchmark_kind'):
            accounting = native.Accounting()
            try:
                native.check(native.bind('QueryInformationJobObject', [wintypes.HANDLE,
                    ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p])(
                    job.handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None))
                job_records.append(dict(kind=job.benchmark_kind, http_request_id=job.benchmark_http,
                    cpu_seconds=(accounting.kernel+accounting.user)/10**7,
                    active=accounting.active, total=accounting.total))
            except Exception as error:
                job_records.append(dict(error=type(error).__name__))
        result = original_close(job)
        if job in app.state.benchmark_jobs:
            app.state.benchmark_jobs.remove(job)
        return result

    native.Job.launch, native.Job.close = launch, close
    admissions = []
    original = chat.admission.acquire

    def acquire(*args, **kwargs):
        result = original(*args, **kwargs)
        current = runtime_metrics.current.get()
        admissions.append(dict(http_request_id=current.get('http_request_id') if current else None))
        return result

    chat.admission.acquire = acquire
    original_generation = completion.generate_response

    def tagged(messages, key, url, model, **kwargs):
        current = runtime_metrics.current.get()
        tag = current['http_request_id'] if current else 'missing'
        return original_generation(messages, key, url, 'mock-' + tag, **kwargs)

    completion.generate_response = tagged
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=settings.APP_PORT,
        workers=1, limit_concurrency=16, timeout_keep_alive=5, access_log=False,
        log_level='warning', timeout_graceful_shutdown=90))
    handles = []

    def snapshot(name):
        worker = app.state.query_worker
        flushed = writer.flush(5)
        save(out / (name + '.json'), dict(writer=writer.snapshot(), flushed=flushed,
            worker=worker.snapshot() if worker else None, admissions=admissions[:],
            resources=samples[:], jobs=job_records[:],
            worker_pids=worker.worker.job.pids() if worker and worker.worker.job else []))

    def control():
        for line in sys.stdin:
            command = line.strip()
            if command == 'ready':
                worker = app.state.query_worker
                handles.extend(native.process_handle(p) for p in worker.worker.job.pids())
                snapshot('api-ready')
            elif command == 'snapshot':
                snapshot('api-snapshot')
            elif command == 'before':
                snapshot('api-before')
            elif command == 'after':
                snapshot('api-after')
            elif command == 'stop':
                server.should_exit = True
                return

    threading.Thread(target=control, daemon=True).start()
    try:
        server.run()
    finally:
        sampling_stop.set()
        sampling_thread.join(3)
        snapshot('api-final')
        worker = app.state.query_worker
        exited = [native.exit_status(handle, 2000)['exited'] for handle in handles]
        for handle in handles:
            native.close(handle)
        with storage_lease(Path(settings.LOCAL_DATA_DIR)):
            lease_released = True
        save(out / 'api-cleanup.json', dict(worker_state=worker.snapshot()['state'] if worker else None,
            job_released=bool(worker and worker.worker.job is None and not worker.worker.gate_held),
            process_exits=exited, storage_lease_released=lease_released))
        engine.dispose()


def environment(storage, url, mock_url, port):
    from cryptography.fernet import Fernet
    env = {k: v for k, v in os.environ.items() if k.upper() in {
        'SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'USERPROFILE',
        'LOCALAPPDATA', 'APPDATA', 'PROGRAMFILES', 'PROGRAMDATA'}}
    env.update(APP_NAME='D1 C1 isolated preflight', APP_PORT=str(port),
        FRONTEND_URL='http://127.0.0.1:3000', DATABASE_URL=url,
        JWT_SECRET_KEY=uuid.uuid4().hex + uuid.uuid4().hex, JWT_ALGORITHM='HS256',
        JWT_EXPIRE_MINUTES='60', ENCRYPTION_KEY=Fernet.generate_key().decode(),
        LOCAL_DATA_DIR=str(storage), EMBEDDING_BACKEND='fastembed',
        EMBEDDING_MODEL='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2',
        PERSISTENT_QUERY_WORKER_ENABLED='true', RUNTIME_METRICS_ENABLED='true',
        SESSION_CHAT_CONCURRENCY_ENABLED='false', LLM_BACKEND='provider',
        LLM_ALLOWED_BASE_URLS=mock_url, PROVIDER_IS_SIMULATED='false',
        CITATION_AUDIT_ENABLED='false', STARTUP_RECOVERY_ENABLED='false',
        WORKSPACE_DELETE_MAINTENANCE_MODE='false', RECOVERY_SINGLE_PROCESS_CONFIRMED='false',
        HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', PYTHONDONTWRITEBYTECODE='1')
    return env


def main(out, measure_gate=None):
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    budget_start = float(os.environ.get('ASTERIA_BENCHMARK_STARTED', started))
    result = dict(verdict='NOT READY', phase='environment', measured_requests=0,
                  queries=[], cleanup={})
    if measure_gate:
        gate = json.loads((measure_gate / 'result.json').read_text())
        assert gate['verdict'] == 'PREFLIGHT PASSED' and len(gate['queries']) == 60
        cleanup = json.loads((measure_gate / 'api-cleanup.json').read_text())
        assert cleanup['job_released'] and all(cleanup['process_exits'])
        assert json.loads((measure_gate / 'dataset.json').read_text()) == dataset()
    pg = proc = engine = mock = None
    log = None
    calls = []
    data = dataset()
    save(out / 'dataset.json', data)
    save(out / 'source.json', dict(head=subprocess.check_output(['git', 'rev-parse', 'HEAD'],
        cwd=ROOT, text=True).strip(), branch=subprocess.check_output(['git', 'branch', '--show-current'],
        cwd=ROOT, text=True).strip(),
        tracked_diff=subprocess.check_output(['git', 'diff', '--stat'], cwd=ROOT, text=True),
        hashes={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in [*Path(__file__).parent.glob('rag_c1*.py'),
                          BACKEND / 'docs/benchmark-workload-design.md', BACKEND / 'requirements.txt']},
        dataset_sha256=digest(json.dumps(data, sort_keys=True)),
        platform=platform.platform(), python=platform.python_version(),
        packages={d.metadata['Name']: d.version for d in importlib.metadata.distributions()}))
    try:
        resources = memory()
        save(out / 'resources-before.json', resources)
        assert resources['available'] >= 6 * 2**30, 'RAM preflight threshold'
        assert resources['disk_free'] >= 10 * 2**30, 'Disk preflight threshold'
        image = json.loads(docker('image', 'inspect', IMAGE))[0]
        save(out / 'docker-image.json', {k: image[k] for k in ['Id', 'RepoDigests', 'Architecture', 'Os']})
        name = 'asteria-d1-preflight-' + uuid.uuid4().hex[:10]
        pg = docker('run', '-d', '--pull=never', '--name', name,
                    '--label', 'asteria.experiment=d1-c1-preflight',
                    '-p', '127.0.0.1::5432', '-e', 'POSTGRES_HOST_AUTH_METHOD=trust',
                    '-e', 'POSTGRES_DB=benchmark_d1', '-e', 'PGDATA=/tmp/d1/pgdata', IMAGE)
        info = json.loads(docker('inspect', pg))[0]
        port = info['NetworkSettings']['Ports']['5432/tcp'][0]['HostPort']
        url = f'postgresql+psycopg://postgres@127.0.0.1:{port}/benchmark_d1'
        from sqlalchemy import create_engine, text
        engine = create_engine(url, connect_args={'connect_timeout': 5})
        deadline = time.monotonic() + 30
        while True:
            try:
                with engine.connect() as db:
                    assert db.scalar(text('SHOW data_directory')) == '/tmp/d1/pgdata'
                    version = db.scalar(text('SHOW server_version'))
                    assert version.startswith('18.6'), 'PostgreSQL version differs'
                    result['postgres'] = version
                    save(out / 'postgres.json', dict(container_id=pg, name=name, version=version,
                        port=port, settings={k: db.scalar(text('SHOW ' + k)) for k in [
                            'max_connections', 'shared_buffers', 'work_mem', 'synchronous_commit',
                            'fsync', 'default_transaction_isolation', 'data_directory']}))
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise
                threading.Event().wait(.1)

        by_question = {q['question']: q for q in data['queries']}

        class Mock(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                tick = time.monotonic()
                record = dict(start=tick, valid=False)
                calls.append(record)
                try:
                    body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    record['model_tag'] = body['model']
                    context = json.loads(body['messages'][1]['content'])
                    q = by_question[context['question']]
                    record['query_id'] = q['id']
                    matching = [e for e in context['evidence'] if
                        e.get('document_name') == q['document'] and q['fact'] in e['content']]
                    record['evidence_count'] = len(context['evidence'])
                    if not matching:
                        raise ValueError('oracle mismatch')
                    citation = matching[0]['number']
                    answer = json.dumps(dict(answer=q['answer'] + f' [{citation}]',
                                             citations=[citation], basis='grounded'))
                    record.update(valid=True, answer_sha256=digest(answer), citation=citation)
                    threading.Event().wait(.2)
                    response = dict(id='local', object='chat.completion', created=0,
                        model=body['model'], choices=[dict(index=0, finish_reason='stop',
                        message=dict(role='assistant', content=answer))])
                    status = 200
                except Exception as error:
                    record['error_type'] = type(error).__name__
                    response, status = {'error': {'message': 'Synthetic oracle mismatch'}}, 400
                encoded = json.dumps(response).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                record['elapsed_ms'] = (time.monotonic() - tick) * 1000

        mock = ThreadingHTTPServer(('127.0.0.1', 0), Mock)
        threading.Thread(target=mock.serve_forever, daemon=True).start()
        storage = out / 'storage'
        cache = ROOT / 'data/mvp-test/models/models--Qdrant--paraphrase-multilingual-MiniLM-L12-v2-onnx-Q'
        shutil.copytree(cache, storage / 'models' / cache.name)
        save(out / 'model-manifest.json', {str(p.relative_to(cache)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in cache.rglob('*') if p.is_file()})
        api_port = free_port()
        env = environment(storage, url, f'http://127.0.0.1:{mock.server_port}/v1', api_port)
        # Explicit settings exclude inherited/development env values; imports happen only now.
        os.environ.update(env)
        sys.path.insert(0, str(BACKEND))
        import app.main  # noqa: F401 -- register the complete application model metadata
        from app.core.config import Settings
        config = Settings(_env_file=None)
        save(out / 'configuration.json', {k: v for k, v in config.model_dump().items()
            if k not in {'JWT_SECRET_KEY', 'ENCRYPTION_KEY', 'DATABASE_URL'}})
        log = (out / 'api.log').open('w', encoding='utf-8')
        proc = subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()),
            '--api', '--output', str(out)], env=env, cwd=out, stdin=subprocess.PIPE,
            stdout=log, stderr=log, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        import httpx
        with httpx.Client(base_url=f'http://127.0.0.1:{api_port}', timeout=90, trust_env=False) as client:
            deadline = time.monotonic() + 60
            while True:
                try:
                    assert client.get('/api/ready', timeout=2).status_code == 200
                    break
                except Exception:
                    if proc.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('API readiness not established') from None
                    threading.Event().wait(.1)
            proc.stdin.write('ready\n')
            proc.stdin.flush()
            credentials = dict(email='d1-synthetic@example.com', password='Synthetic-only-123')
            response = client.post('/api/register', json=credentials)
            assert response.status_code == 200, 'register failed'
            uid = response.json()['id']
            response = client.post('/api/login', json=credentials)
            assert response.status_code == 200, 'login failed'
            client.headers['Authorization'] = 'Bearer ' + response.json()['access_token']
            from sqlalchemy.orm import Session
            from app.models.user_llm_settings import UserLLMSetting
            from app.core.security import encrypt_text
            with Session(engine) as db:
                db.add(UserLLMSetting(id=str(uuid.uuid4()), user_id=uuid.UUID(uid), provider='mock',
                    base_url=env['LLM_ALLOWED_BASE_URLS'], model_name='mock',
                    encrypted_api_key=encrypt_text('synthetic-only')))
                db.commit()
            response = client.post('/api/workspaces', json={'name': 'D1 synthetic'})
            assert response.status_code == 200
            wid = response.json()['id']
            result['phase'] = 'indexing'
            documents = {}
            for doc in data['documents']:
                response = client.post(f'/api/workspaces/{wid}/documents',
                    files={'file': (doc['name'], doc['content'].encode(), 'text/plain')})
                item = dict(name=doc['name'], status=response.status_code)
                result.setdefault('uploads', []).append(item)
                assert response.status_code == 200, 'upload failed'
                item.update(document_id=response.json()['id'], index_status=response.json()['status'])
                assert item['index_status'] == 'ready', 'indexing did not reach ready'
                documents[doc['name']] = item['document_id']
                print('indexed', len(documents), flush=True)
                assert time.monotonic() - budget_start < 1500, '25 minute budget exhausted'
            with engine.connect() as db:
                rows = db.execute(text('SELECT document_id, ordinal, content, vector FROM document_chunks ORDER BY document_id, ordinal')).mappings().all()
                assert len(rows) == 200, 'expected 200 chunks'
                models = db.execute(text('SELECT DISTINCT embedding_model, status, chunk_count FROM documents')).all()
                save(out / 'database-model-identity.json', [list(row) for row in models])
                verify_index_identity(models, config.EMBEDDING_BACKEND, config.EMBEDDING_MODEL)
                result['index_identity'] = dict(model=f'{config.EMBEDDING_BACKEND}:{config.EMBEDDING_MODEL}', documents=20, chunks=200,
                    dimensions=sorted({len(row['vector']) for row in rows}))
                manifests = []
                for doc in data['documents']:
                    chunks = [r for r in rows if r['document_id'] == documents[doc['name']]]
                    assert [digest(c['content']) for c in chunks] == doc['chunk_hashes']
                    manifests.append(dict(name=doc['name'], document_id=documents[doc['name']],
                        chunks=[dict(ordinal=c['ordinal'], length=len(c['content']),
                            content_hash=digest(c['content']), dimensions=len(c['vector']),
                            vector_hash=digest(json.dumps(c['vector']))) for c in chunks]))
                save(out / 'indexed-manifest.json', manifests)
                if measure_gate:
                    previous = json.loads((measure_gate / 'indexed-manifest.json').read_text())
                    assert [(d['name'], d['chunks']) for d in manifests] == [
                        (d['name'], d['chunks']) for d in previous], 'Frozen vector/content identity differs'
                    result['vector_identity_matches_preflight'] = True
            result['phase'] = '60-query-preflight'
            # Pre-created independent sessions with the same fixed historical pair.
            from datetime import datetime, timezone, timedelta
            from app.models.chat_sessions import ChatSession
            from app.models.messages import Message
            from app.models.session_profile import SessionProfile
            from app.models.knowledge import SessionWorkspace
            sessions = [str(uuid.uuid4()) for _ in range(360 if measure_gate else 60)]
            with Session(engine) as db:
                for sid in sessions:
                    db.add(ChatSession(id=sid, user_id=uuid.UUID(uid), title='D1 independent'))
                    db.flush()
                    db.add(SessionProfile(session_id=sid, session_type='questioning'))
                    db.add(SessionWorkspace(session_id=sid, workspace_id=wid))
                    for n, (role, content) in enumerate([
                        ('user', 'I am studying the synthetic facility manuals.'),
                        ('assistant', 'We can examine their facts using the workspace documents.')]):
                        db.add(Message(id=str(uuid.uuid4()), session_id=sid, role=role, content=content,
                            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=n)))
                db.commit()
            warmup_start = time.monotonic()
            for q, sid in zip(data['queries'], sessions):
                rid = str(uuid.uuid4())
                before = len(calls)
                response = client.post('/api/chat', json=dict(message=q['question'],
                    workspace_id=wid, session_id=sid, answer_mode='document',
                    session_type='questioning', learning_mode='direct', request_id=rid))
                record = dict(query_id=q['id'], request_id=rid, session_id=sid,
                    http_status=response.status_code, trace_id=response.headers.get('x-asteria-trace'),
                    provider_calls=len(calls)-before)
                result['queries'].append(record)
                assert response.status_code == 200, 'Chat preflight HTTP failure'
                body = response.json()
                record['answer_basis'] = body.get('answer_basis')
                record['oracle_ok'] = q['fact'] in body['answer'] and any(
                    s['document_id'] == documents[q['document']] and q['fact'] in s['content']
                    for s in body['sources'])
                assert record['oracle_ok'], 'answer/evidence oracle failed'
                assert len(calls)-before == 1 and calls[-1]['valid'], 'Provider call count/oracle mismatch'
                with engine.connect() as db:
                    turns = db.execute(text('SELECT session_id, question, response FROM chat_turns WHERE request_id=:r'), {'r': rid}).mappings().all()
                    assert len(turns) == 1 and turns[0]['session_id'] == sid and turns[0]['question'] == q['question']
                    assert turns[0]['response']['answer'] == body['answer']
                    assert db.scalar(text('SELECT count(*) FROM messages WHERE session_id=:s'), {'s': sid}) == 4
                    assert db.scalar(text('SELECT count(*) FROM message_evidence e JOIN messages m ON m.id=e.message_id WHERE m.session_id=:s'), {'s': sid}) == 1
                record['database_ok'] = True
                if measure_gate:
                    assert time.monotonic() - warmup_start <= 180, 'Warmup exceeded 180 seconds'
                assert time.monotonic() - budget_start < 1500, '25 minute budget exhausted'
                print('preflight query', len(result['queries']), 'passed', flush=True)
            result['verdict'] = 'PREFLIGHT PASSED'
            if measure_gate:
                result['warmup_seconds'] = time.monotonic() - warmup_start
                from rag_c1_measure import measure
                result['phase'] = 'measurement'
                result['measurement'] = measure(client, engine, proc, out, data, sessions[60:],
                    wid, documents, calls, pg, budget_start)
                result['measured_requests'] = result['measurement']['sent']
                result['verdict'] = 'PASS'
    except Exception as error:
        result['verdict'] = 'FAIL' if result['phase'] == 'measurement' else 'NOT READY'
        result['failure'] = dict(type=type(error).__name__,
            reason=str(error) if isinstance(error, (AssertionError, RuntimeError)) else 'See phase; unrestricted exception excluded')
        if (out / 'requests.jsonl').exists():
            result['measured_requests'] = len((out / 'requests.jsonl').read_text().splitlines())
    finally:
        if proc:
            if proc.poll() is None:
                proc.stdin.write('stop\n')
                proc.stdin.flush()
                try:
                    proc.wait(timeout=100)
                except subprocess.TimeoutExpired:
                    result['cleanup']['api_exit_unknown'] = True
                    # Do not force a new Worker or silently proceed after uncertain cleanup.
            result['cleanup']['api_exit_code'] = proc.poll()
        if log:
            log.close()
        if mock:
            mock.shutdown()
            mock.server_close()
        if engine:
            engine.dispose()
        if pg:
            docker('stop', '--time=3', pg)
            docker('rm', '-v', pg)
            result['cleanup']['postgres_removed'] = not docker('ps', '-a', '--filter', 'id='+pg, '--format', '{{.ID}}')
        save(out / 'mock-calls.json', calls)
        result['wall_seconds'] = time.monotonic() - started
        save(out / 'result.json', result)
    print(json.dumps({'verdict': result['verdict'], 'phase': result['phase'], 'queries': len(result['queries'])}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--api', action='store_true')
    parser.add_argument('--measure-gate', type=Path)
    args = parser.parse_args()
    if args.api:
        api(args.output.resolve())
    else:
        main(args.output.resolve(), args.measure_gate.resolve() if args.measure_gate else None)
