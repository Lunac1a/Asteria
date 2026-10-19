"""Predeclared burst workload; no response-dependent arrival staggering or retries."""
from concurrent.futures import ThreadPoolExecutor
import os
import platform
import threading
import time


def run(ns):
    # Reuse the real HTTP/auth/model/database fixture and its independent verifier.
    settings, evidence = ns['settings'], ns['evidence']
    native, resources, engine = ns['native'], ns['resources'], ns['engine']
    evidence['load_plan'] = dict(levels=[1, 2, 4], rounds=10, arrival='barrier burst',
        mock_delay_seconds=2, order='AB on even rounds, BA on odd rounds',
        python=platform.python_version(), platform=platform.platform(), cpus=os.cpu_count(),
        pool_size=engine.pool.size(), pool_max_overflow=engine.pool._max_overflow,
        pool_timeout=engine.pool.timeout(), session_wait_seconds=settings.SESSION_LOCK_WAIT_SECONDS,
        latency='HTTP client construction plus TCP request; verification excluded',
        sampling_seconds=.2)
    samples = evidence['load_resources'] = []
    batches = evidence['load_batches'] = []
    current = {'batch': 'warmup'}
    stop = threading.Event()

    def sample():
        while not stop.is_set():
            row = {'at': time.monotonic(), 'batch': current['batch'], 'processes': []}
            try:
                groups = {'api_and_harness': [os.getpid()],
                    'embedding': ns['app'].state.query_worker.worker.job.pids(), 'provider': []}
                for job in list(ns['provider_jobs']):
                    if job.handle:
                        try:
                            groups['provider'].extend(job.pids())
                        except OSError:
                            pass  # A Job may close between these two observations.
                for kind, pids in groups.items():
                    for proc in resources.f.snapshot(set(pids)):
                        h = native.check(native.open_process(0x101011, False, proc['pid']))
                        try:
                            proc.update(resources.metrics(h), kind=kind)
                        finally:
                            native.close(h)
                        row['processes'].append(proc)
                row['pool_checked_out'] = engine.pool.checkedout()
                with ns['observer'].connect() as db:
                    row['postgres_connections'] = [dict(r._mapping) for r in db.execute(ns['f'].text(
                        "SELECT state, count(*) AS count FROM pg_stat_activity "
                        "WHERE datname=current_database() AND pid<>pg_backend_pid() GROUP BY state"))]
            except Exception as exc:
                row['sampling_error'] = repr(exc)
            samples.append(row)
            stop.wait(.2)

    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            for enabled in (False, True):
                settings.SESSION_CHAT_CONCURRENCY_ENABLED = enabled
                key = 'load-warm-' + str(enabled)
                ns['prepare'](key)
                ns['releases'][key].set()
                sid = ns['seed']()
                body = ns['payload'](sid)
                assert ns['send'](key, body).status_code == 200
                ns['verify'](sid, body, True)
            for level in (1, 2, 4):
                for repeat in range(10):
                    for enabled in ((False, True) if repeat % 2 == 0 else (True, False)):
                        settings.SESSION_CHAT_CONCURRENCY_ENABLED = enabled
                        name = f'load-{level}-{repeat}-{enabled}'
                        current['batch'] = name
                        cohort = []
                        for i in range(level):
                            key, sid = name + '-' + str(i), ns['seed']()
                            ns['prepare'](key)
                            ns['releases'][key].set()  # No stage gate in measured requests.
                            cohort.append((key, sid, ns['payload'](sid)))
                        gate = threading.Barrier(level + 1, timeout=5)
                        def request(item):
                            gate.wait()
                            return ns['send'](item[0], item[2])
                        futures = [pool.submit(request, item) for item in cohort]
                        start = time.monotonic()
                        gate.wait()
                        responses = [future.result(30) for future in futures]
                        end = time.monotonic()
                        for (_, sid, body), response in zip(cohort, responses):
                            assert response.status_code in (200, 429), (response.status_code, response.text)
                            ns['verify'](sid, body, response.status_code == 200)
                        batches.append(dict(name=name, enabled=enabled, level=level, repeat=repeat,
                            start=start, end=end, labels=[item[0] for item in cohort],
                            statuses=[r.status_code for r in responses]))
                        assert ns['b'].chat.admission.snapshot()['shared'] == 0
                        assert ns['sc'].coordinator.snapshot() == {}
                        print(name, batches[-1]['statuses'], flush=True)
    finally:
        stop.set()
        thread.join(10)
        assert not thread.is_alive()
