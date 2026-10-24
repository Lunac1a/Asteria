"""One fixed S1 capacity repetition; only the frozen Provider protocol varies."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import rag_c1_preflight as fixture
import rag_c1_measure
import rag_s1_15_measure
from provider_profile_hooks import replace_once


def child_source(source, backend, protocol):
    source = replace_once(source, 'str(Path(__file__).resolve().parents[3])', repr(str(backend)))
    # Same snapshot loader as Step 7, without internal profiling hooks.
    loader = ("import importlib.util as _iu\n"
        "_spec = _iu.spec_from_file_location('app.services.llm.protocol', " + repr(str(protocol)) + ")\n"
        "_protocol = _iu.module_from_spec(_spec)\n"
        "sys.modules[_spec.name] = _protocol\n"
        "_spec.loader.exec_module(_protocol)\n")
    return replace_once(source, 'from app.services.llm.protocol import GenerationError, GenerationText',
                        loader+'from app.services.llm.protocol import GenerationError, GenerationText')


def install(out):
    from app.services.llm import provider
    freeze = json.loads((out.parent/'freeze.json').read_text())
    assert out.name in freeze['limits']['order']
    version = 'original' if out.name.startswith('A') else 'optimized'
    protocol = out.parent/f'protocol-{version}.py'
    assert hashlib.sha256(protocol.read_bytes()).hexdigest() == freeze['protocols'][version]
    folder = out/'provider-source'
    folder.mkdir()
    child = folder/'provider.py'
    child.write_text(child_source(Path(provider.__file__).read_text(), fixture.BACKEND, protocol), encoding='utf-8')
    (folder/'identity.json').write_text(json.dumps(dict(version=version,
        protocol_sha256=hashlib.sha256(protocol.read_bytes()).hexdigest(),
        generated_child_sha256=hashlib.sha256(child.read_bytes()).hexdigest()), indent=2))
    original_run = provider.run_provider

    def run(command, **kwargs):
        return original_run([command[0], str(child)], **kwargs)

    provider.run_provider = run


def measure(client, engine, proc, out, data, sessions, wid, documents, calls, pg, budget_start):
    from sqlalchemy import text
    # Additional integrity checks outside the measured arrival window only.
    def snapshot():
        with engine.connect() as db:
            owners = {row.id: tuple(row) for row in db.execute(text('SELECT id, user_id, title, created_at, updated_at FROM chat_sessions'))}
            messages = {row.id: tuple(row) for row in db.execute(text('SELECT id, session_id, role, content, created_at FROM messages'))}
            owner = db.scalar(text('SELECT user_id FROM workspaces WHERE id=:w'), {'w': wid})
        return owners, messages, owner

    owners, old_messages, owner = snapshot()
    result = rag_s1_15_measure.measure(client, engine, proc, out, data, sessions, wid, documents, calls, pg, budget_start)
    after, messages, after_owner = snapshot()
    rows = json.loads((out/'verified-requests.json').read_text())
    success_sessions = {r['session_id'] for r in rows if r['status'] == 200}
    assert owners.keys() == after.keys() and owner == after_owner
    assert all(after[key][:4] == value[:4] for key, value in owners.items()), 'Session ownership or identity changed'
    assert all(after[key] == value for key, value in owners.items() if key not in success_sessions), 'Rejected/unused session changed'
    assert all(messages.get(key) == value for key, value in old_messages.items()), 'Existing message changed'
    queries = {q['id']: q for q in data['queries']}
    with engine.connect() as db:
        for row in rows:
            sid = row['session_id']
            assert db.scalar(text('SELECT user_id FROM chat_sessions WHERE id=:s'), {'s': sid}) == owner
            assert db.scalar(text('SELECT workspace_id FROM session_workspaces WHERE session_id=:s'), {'s': sid}) == wid
            if row['status'] == 200:
                assert db.scalar(text('SELECT user_id FROM chat_turns WHERE request_id=:r'), {'r': row['request_id']}) == owner
                new_messages = sorted((value[2], value[3]) for key, value in messages.items()
                                      if key not in old_messages and value[1] == sid)
                assert new_messages == sorted([('user', queries[row['query_id']]['question']),
                    ('assistant', json.loads(row['response_body'])['answer'])]), 'Persisted message content differs'
    (out/'ownership-integrity.json').write_text(json.dumps(dict(
        all_submitted_verified=len(rows), session_workspace_ownership=True,
        existing_messages_unchanged=True, session_identity_unchanged=True), indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--measure-gate', type=Path)
    parser.add_argument('--api', action='store_true')
    args = parser.parse_args()
    out = args.output.resolve()
    if args.api:
        sys.path.insert(0, str(fixture.BACKEND))
        install(out)
        fixture.api(out)
    else:
        source = replace_once(Path(fixture.__file__).read_text(),
            "[sys.executable, '-B', str(Path(__file__).resolve()),",
            "[sys.executable, '-B', "+repr(str(Path(__file__).resolve()))+",")
        namespace = dict(__name__='capacity_fixture', __file__=fixture.__file__)
        exec(compile(source, fixture.__file__, 'exec'), namespace)
        original_environment = namespace['environment']

        def environment(*values):
            result = original_environment(*values)
            result['SESSION_CHAT_CONCURRENCY_ENABLED'] = 'true'
            return result

        namespace['environment'] = environment
        rag_c1_measure.measure = measure
        namespace['main'](out, args.measure_gate.resolve())
