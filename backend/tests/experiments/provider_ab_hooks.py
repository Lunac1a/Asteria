"""Benchmark-only numeric lifecycle marks; no payload or exception capture."""
import contextvars
import json
from pathlib import Path
import threading
import time
import uuid

ORDER = ['original_control', 'optimized_control', 'original_profile', 'optimized_profile',
         'optimized_profile', 'original_profile', 'optimized_control', 'original_control'] * 3
ACTIVE = contextvars.ContextVar('provider_profile', default=None)


def trace_for(out, index):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, str(out.resolve())+':provider-profile:'+str(index)))


def replace_once(source, old, new):
    assert source.count(old) == 1, old
    return source.replace(old, new)


def child_source(source, backend):
    prefix = '''import time as _pt
_marks = []
def _stamp(name):
    _marks.append(dict(name=name, wall_ns=_pt.perf_counter_ns(), cpu_ns=_pt.process_time_ns()))
_stamp('child_entry')
'''
    source = prefix+source
    source = replace_once(source, 'str(Path(__file__).resolve().parents[3])', repr(str(backend)))
    for line, name in [
        ('from app.services.llm.protocol import GenerationError, GenerationText', 'protocol_import'),
        ('from httpx import Client', 'httpx_import'),
        ('from openai import OpenAI, APIConnectionError, APIStatusError, RateLimitError, APITimeoutError', 'openai_import'),
        ('from app.services.runtime_metrics import provider_call', 'metrics_import'),
        ('from app.services.llm.process import run_provider', 'process_import')]:
        source = replace_once(source, line, f"_stamp('{name}_begin')\n{line}\n_stamp('{name}_end')")
    source = replace_once(source, '    with OpenAI(', "    _stamp('sdk_init_begin')\n    with OpenAI(")
    source = replace_once(source, '    ) as client:\n        response = client.chat.completions.create(',
        "    ) as client:\n        _stamp('sdk_init_end')\n        _stamp('sdk_resource_begin')\n        _create = client.chat.completions.create\n        _stamp('sdk_resource_end')\n        _stamp('sdk_create_begin')\n        response = _create(")
    source = replace_once(source, '        return decode_response(response, started)',
        "        _stamp('sdk_create_end')\n        _stamp('decode_begin')\n        decoded = decode_response(response, started)\n        _stamp('decode_end')\n        return decoded")
    source = replace_once(source, '        from app.services.knowledge.index_worker import limit_memory',
        "        _stamp('limit_import_begin')\n        from app.services.knowledge.index_worker import limit_memory\n        _stamp('limit_import_end')")
    source = replace_once(source, '        limit_memory()\n        answer = request_provider(**json.loads(sys.stdin.read()))',
        "        _stamp('limit_install_begin')\n        limit_memory()\n        _stamp('limit_install_end')\n        _stamp('stdin_read_begin')\n        _raw = sys.stdin.read()\n        _stamp('stdin_read_end')\n        _stamp('input_json_begin')\n        _payload = json.loads(_raw)\n        _stamp('input_json_end')\n        _stamp('request_begin')\n        answer = request_provider(**_payload)\n        _stamp('request_end')")
    source = replace_once(source, '        print(json.dumps({"answer": str(answer), "metadata": answer.metadata}))',
        "        _stamp('output_json_begin')\n        _encoded = json.dumps({'answer': str(answer), 'metadata': answer.metadata})\n        _stamp('output_json_end')\n        _stamp('stdout_begin')\n        print(_encoded)\n        _stamp('stdout_end')")
    # send includes full HTTP body read; SDK parsing is nested inside create, not HTTP.
    hooks = '''
_stamp('observer_install_begin')
_send = Client.send
def _timed_send(self, *a, **kw):
    _stamp('http_send_begin')
    try:
        return _send(self, *a, **kw)
    finally:
        _stamp('http_send_end')
Client.send = _timed_send
from openai._response import APIResponse
_parse = APIResponse.parse
def _timed_parse(self, *a, **kw):
    _stamp('sdk_parse_begin')
    try:
        return _parse(self, *a, **kw)
    finally:
        _stamp('sdk_parse_end')
APIResponse.parse = _timed_parse
_stamp('observer_install_end')
'''
    source = replace_once(source, 'if __name__ == "__main__":', hooks+'\nif __name__ == "__main__":')
    source += "\n    finally:\n        _stamp('child_final')\n        Path(os.environ['ASTERIA_PROFILE_FILE']).write_text(json.dumps(_marks), encoding='utf-8')\n"
    compile(source, '<profiled-provider>', 'exec')
    return source


def mark(events, name):
    events.append(dict(name=name, wall_ns=time.perf_counter_ns(), cpu_thread_ns=time.thread_time_ns(),
                       thread=threading.get_ident()))


def process_source(source):
    source = source[source.index('def run_provider('):]
    source = replace_once(source, '    if os.name != "nt":', "    _pe = _profile_active.get()['events']\n    if os.name != \"nt\":")
    for statement, name, indent in [
        ('job = native.Job()', 'job_create', 4),
        ('launcher, pid, stdin, stdout = job.launch(command, env)', 'job_launch', 8),
        ('stdin.write(input)', 'stdin_write', 16),
        ('stdin.close()  # Child reads stdin until EOF.', 'stdin_close', 16),
        ('value = stdout.read(1024 * 1024 + 1)', 'stdout_read', 16),
        ('output = mailbox.get(timeout=max(.001, deadline - time.monotonic()))', 'mailbox_wait', 12),
        ('status = native.exit_status(launcher, max(0, int((deadline-time.monotonic()) * 1000)))', 'launcher_exit', 8)]:
        pad = ' '*indent
        source = replace_once(source, pad+statement,
            pad+f"_profile_stamp(_pe, '{name}_begin')\n"+pad+statement+'\n'+pad+f"_profile_stamp(_pe, '{name}_end')")
    source = replace_once(source, '        cleanup_deadline =', "        _profile_stamp(_pe, 'cleanup_begin')\n        cleanup_deadline =")
    source = replace_once(source, '            job.close()', "            job.close()\n            _profile_stamp(_pe, 'cleanup_end')")
    compile(source, '<profiled-process>', 'exec')
    return source


def install(out):
    from app.services.llm import provider, process, completion
    from app.services import runtime_metrics
    backend = Path(provider.__file__).resolve().parents[3]
    folder = out/'profiling'
    folder.mkdir()
    children = {}
    for mode in dict.fromkeys(ORDER):
        target = folder/mode
        target.mkdir()
        protocol = out.parent/('protocol-original.py' if mode.startswith('original') else 'protocol-optimized.py')
        source = Path(provider.__file__).read_text()
        if mode.endswith('profile'):
            source = child_source(source, backend)
        else:
            source = replace_once(source, 'str(Path(__file__).resolve().parents[3])', repr(str(backend)))
        # Identical file-loading shim in both arms; only the frozen protocol differs.
        loader = ("import importlib.util as _iu\n"
            "_spec = _iu.spec_from_file_location('app.services.llm.protocol', " + repr(str(protocol)) + ")\n"
            "_protocol = _iu.module_from_spec(_spec)\n"
            "sys.modules[_spec.name] = _protocol\n"
            "_spec.loader.exec_module(_protocol)\n")
        source = replace_once(source, 'from app.services.llm.protocol import GenerationError, GenerationText',
                              loader+'from app.services.llm.protocol import GenerationError, GenerationText')
        child = target/'provider.py'
        child.write_text(source, encoding='utf-8')
        children[mode] = child
    transformed = process_source(Path(process.__file__).read_text())
    (folder/'process_instrumented.txt').write_text(transformed, encoding='utf-8')
    namespace = dict(process.__dict__, _profile_stamp=mark, _profile_active=ACTIVE)
    exec(compile(transformed, str(folder/'process_instrumented.txt'), 'exec'), namespace)
    timed_process = namespace['run_provider']
    original_run, original_generate = provider.run_provider, completion.generate_response
    original_completion = completion._provider_completion
    selected = {trace_for(out, i): (i, mode) for i,mode in enumerate(ORDER)}

    def run(command, **kwargs):
        record = ACTIVE.get()
        if record is None:
            return original_run(command, **kwargs)
        mark(record['events'], 'run_begin')
        try:
            command = [command[0], str(children[record['mode']])]
            if record['mode'].endswith('profile'):
                kwargs['env'] = dict(kwargs['env'], ASTERIA_PROFILE_FILE=str(folder/(record['http_request_id']+'-child.json')))
                return timed_process(command, **kwargs)
            return original_run(command, **kwargs)
        finally:
            mark(record['events'], 'run_end')

    def generate(*args, **kwargs):
        record = ACTIVE.get()
        if record is None:
            return original_generate(*args, **kwargs)
        mark(record['events'], 'provider_begin')
        try:
            return original_generate(*args, **kwargs)
        finally:
            mark(record['events'], 'provider_end')

    def configured(*args, **kwargs):
        context = runtime_metrics.current.get() or {}
        chosen = selected.get(context.get('trace_id'))
        if chosen is None:
            return original_completion(*args, **kwargs)
        index, mode = chosen
        record = dict(index=index, mode=mode, http_request_id=context['http_request_id'], events=[])
        token = ACTIVE.set(record)
        mark(record['events'], 'completion_begin')
        try:
            return original_completion(*args, **kwargs)
        finally:
            mark(record['events'], 'completion_end')
            ACTIVE.reset(token)
            (folder/(record['http_request_id']+'-parent.json')).write_text(json.dumps(record), encoding='utf-8')

    from app.services.knowledge import windows_job as native
    import ctypes
    from ctypes import wintypes
    original_close = native.Job.close

    def close(job):
        record = ACTIVE.get()
        if record is not None and job.handle:
            value = native.Extended()
            native.check(native.bind('QueryInformationJobObject', [wintypes.HANDLE,
                ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p])(
                job.handle, 9, ctypes.byref(value), ctypes.sizeof(value), None))
            record['job_peak_process_bytes'] = value.peak_process
            record['job_peak_bytes'] = value.peak_job
        return original_close(job)

    native.Job.close = close
    provider.run_provider = run
    completion.generate_response = generate
    completion._provider_completion = configured
