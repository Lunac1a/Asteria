"""Isolated Windows provider failure experiment; never imports an engine or database.

Run from repository root with backend/.venv/Scripts/python.exe -B <this file>.
Only the outer observer owns the safety Job; it deliberately keeps it open during
fault observation. Production generate_response and its child remain unchanged.
"""
import argparse
from contextlib import nullcontext
import ctypes as c
from ctypes import wintypes as w
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import platform
import queue
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = Path(__file__).resolve()
sys.path.insert(0, str(ROOT / "backend"))
from app.services.knowledge.windows_job import (  # noqa: E402
    Job, bind, check, close, exit_status, process_handle, terminate,
)


def emit(**value):
    print(json.dumps({"at": time.monotonic(), **value}), flush=True)


class Entry(c.Structure):
    _fields_ = [("size", w.DWORD), ("usage", w.DWORD), ("pid", w.DWORD),
                ("heap", c.c_size_t), ("module", w.DWORD), ("threads", w.DWORD),
                ("ppid", w.DWORD), ("priority", w.LONG), ("flags", w.DWORD),
                ("exe", w.WCHAR * 260)]


def snapshot(pids):
    handle = bind("CreateToolhelp32Snapshot", [w.DWORD, w.DWORD], w.HANDLE)(2, 0)
    if handle == c.c_void_p(-1).value:
        raise c.WinError(c.get_last_error())
    result = []
    try:
        entry = Entry()
        entry.size = c.sizeof(entry)
        more = bind("Process32FirstW", [w.HANDLE, c.POINTER(Entry)])(handle, c.byref(entry))
        while more:
            if entry.pid in pids:
                item = {"pid": entry.pid, "ppid": entry.ppid, "threads": entry.threads,
                        "exe": entry.exe, "at": time.monotonic()}
                ph = process_handle(entry.pid)
                try:
                    count = w.DWORD()
                    check(bind("GetProcessHandleCount", [w.HANDLE, c.POINTER(w.DWORD)])(ph, c.byref(count)))
                    item["handles"] = count.value
                    path = c.create_unicode_buffer(32768)
                    size = w.DWORD(len(path))
                    check(bind("QueryFullProcessImageNameW", [w.HANDLE, w.DWORD, w.LPWSTR,
                                                             c.POINTER(w.DWORD)])(ph, 0, path, c.byref(size)))
                    item["image"] = path.value
                finally:
                    close(ph)
                result.append(item)
            more = bind("Process32NextW", [w.HANDLE, c.POINTER(Entry)])(handle, c.byref(entry))
    finally:
        close(handle)
    return result


def caller(args):
    # Empty cwd prevents .env discovery. Every required setting is synthetic.
    original_cwd = os.getcwd()
    with nullcontext(args.cwd) as cwd:
        os.chdir(cwd)
        os.environ.update(APP_NAME="isolated-provider-test", APP_PORT="1", FRONTEND_URL="http://127.0.0.1",
                          DATABASE_URL="postgresql://invalid:invalid@127.0.0.1:1/never_connect",
                          JWT_SECRET_KEY="test-only", JWT_ALGORITHM="HS256", JWT_EXPIRE_MINUTES="1",
                          ENCRYPTION_KEY="test-only", LLM_ALLOWED_BASE_URLS=args.url,
                          RUNTIME_METRICS_ENABLED="false")
        from app.services.llm import provider
        from app.schemas.llm_settings import validate_provider_url
        validate_provider_url(args.url)
        original = provider.subprocess.Popen

        class ObservedPopen(original):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                emit(event="provider_launcher", pid=self.pid)

            def kill(self):
                emit(event="subprocess_kill", pid=self.pid)
                return super().kill()

            def __exit__(self, *args):
                result = super().__exit__(*args)
                emit(event="pipes_closed", stdin=self.stdin.closed, stdout=self.stdout.closed,
                     stderr=self.stderr.closed)
                return result

        provider.subprocess.Popen = ObservedPopen
        owned = []
        if args.fixed:
            from app.services.knowledge import windows_job as native
            original_launch, original_kill = native.Job.launch, native.Job.kill

            def launch(job, command, env):
                value = original_launch(job, command, env)
                flags = w.DWORD()
                check(bind("GetHandleInformation", [w.HANDLE, c.POINTER(w.DWORD)])(
                    job.handle, c.byref(flags)))
                limits = native.Extended()
                check(bind("QueryInformationJobObject", [w.HANDLE, c.c_int, c.c_void_p,
                           w.DWORD, c.c_void_p])(job.handle, 9, c.byref(limits), c.sizeof(limits), None))
                owned.append((job, value[2], value[3]))
                emit(event="provider_launcher", pid=value[1], in_job=job.contains(value[0]),
                     job_handle_inheritable=bool(flags.value & 1), job_flags=limits.basic.flags,
                     job=job.accounting())
                return value

            def kill(job):
                emit(event="subprocess_kill", job=job.accounting())
                return original_kill(job)

            native.Job.launch, native.Job.kill = launch, kill
            if args.binding_failure:
                original_bind = native.bind

                def fail_bind(name, *a, **kw):
                    if name == "CreateProcessW":
                        def fail(*a):
                            emit(event="creation_rejected")
                            c.set_last_error(5)
                            return 0
                        return fail
                    return original_bind(name, *a, **kw)
                native.bind = fail_bind
        emit(event="caller_ready", pid=os.getpid(), resources=snapshot({os.getpid()}))
        assert sys.stdin.readline().strip() == "go"
        started = time.monotonic()
        try:
            answer = provider.generate_response([{"role": "user", "content": "Return OK"}],
                                                "local-dummy-key", args.url, "mock-model",
                                                timeout_seconds=args.timeout)
            outcome = {"answer": str(answer)}
        except Exception as exc:
            outcome = {"error_type": type(exc).__name__, "category": getattr(exc, "category", None)}
        emit(event="result", elapsed=time.monotonic() - started, outcome=outcome,
             resources=snapshot({os.getpid()}), python_threads=[t.name for t in threading.enumerate()],
             owned_cleanup=[{"job_closed": job.handle is None, "stdin_closed": si.closed,
                             "stdout_closed": so.closed} for job, si, so in owned])
        sys.stdin.readline()  # Observer inspects resources before orderly caller exit.
        os.chdir(original_cwd)


class Messages:
    def __init__(self, stream):
        self.events = []
        self.queue = queue.Queue()

        def read():
            for line in stream:
                item = json.loads(line)
                self.events.append(item)
                self.queue.put(item)
        self.thread = threading.Thread(target=read, daemon=True)
        self.thread.start()

    def get(self, event, timeout=10):
        deadline = time.monotonic() + timeout
        while True:
            value = self.queue.get(timeout=max(.001, deadline - time.monotonic()))
            if value["event"] == event:
                return value


def experiment(mode, fixed=False):
    entered, release, stop = threading.Event(), threading.Event(), threading.Event()
    server_events = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            server_events.append({"event": "request_received", "at": time.monotonic(), "path": self.path})
            entered.set()
            if not release.wait(12):
                return
            try:
                if mode != "normal":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", "1000000")
                    self.end_headers()
                    # Controlled fault: incomplete JSON, read progress prevents SDK idle timeout.
                    while not stop.is_set():
                        self.wfile.write(b" ")
                        self.wfile.flush()
                        stop.wait(.2)
                else:
                    body = json.dumps({"id": "local", "object": "chat.completion", "created": 0,
                                       "model": "mock-model", "choices": [{"index": 0,
                                       "message": {"role": "assistant", "content": "OK"},
                                       "finish_reason": "stop"}]}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
            except OSError as exc:
                server_events.append({"event": "peer_disconnected", "at": time.monotonic(),
                                      "error": type(exc).__name__})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    job = Job()
    caller_directory = tempfile.TemporaryDirectory(prefix="asteria-provider-observer-")
    handles = {}
    stdin = stdout = messages = None
    record = {"scenario": mode, "started": time.monotonic(), "server_events": server_events}
    watchdog_fired = threading.Event()

    def watchdog():
        watchdog_fired.set()
        job.kill()

    timer = threading.Timer(20, watchdog)
    timer.start()
    try:
        env = {k: v for k, v in os.environ.items() if k.upper() in {
            "SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA"}}
        command = [str(ROOT / "backend/.venv/Scripts/python.exe"), "-B", str(SCRIPT), "--caller",
                   "--url", f"http://127.0.0.1:{server.server_port}/v1", "--timeout", "6",
                   "--cwd", caller_directory.name]
        if fixed:
            command.append("--fixed")
        if mode == "binding_failure":
            command.append("--binding-failure")
        launcher, pid, stdin, stdout = job.launch(command, env)
        handles[pid] = launcher
        messages = Messages(stdout)
        ready = messages.get("caller_ready")
        record["caller_pid"] = ready["pid"]
        stdin.write("go\n")
        stdin.flush()
        if mode == "binding_failure":
            record["result"] = messages.get("result")
            assert record["result"]["outcome"]["category"] == "provider_protocol"
            assert not entered.is_set()
            record["tree_after_rejected_creation"] = snapshot(set(job.pids()))
            record["native_job"] = job.accounting()
            assert record["native_job"]["total_processes"] == 3
            stdin.write("exit\n")
            stdin.flush()
            actual = process_handle(ready["pid"])
            try:
                assert exit_status(actual, 3000)["exited"]
            finally:
                close(actual)
            return record
        launch = messages.get("provider_launcher")
        record["provider_launcher_pid"] = launch["pid"]
        assert entered.wait(8), "Provider never reached mock server"
        for member in job.pids():
            if member not in handles:
                handles[member] = process_handle(member)
        record["active_tree"] = snapshot(set(handles))
        record["active_job"] = job.accounting()
        record["members_in_job"] = {p: job.contains(h) for p, h in handles.items()}
        release.set()
        if mode == "parent_crash":
            record["fault_at"] = time.monotonic()
            check(terminate(handles[ready["pid"]], 93))
            record["caller_exit"] = exit_status(handles[ready["pid"]], 2000)
            # Observation interval only; ordering established by HTTP event and process handle.
            if fixed:
                deadline = time.monotonic() + 5
                record["automatic_exit"] = {p: exit_status(h, max(0, int(
                    (deadline-time.monotonic()) * 1000))) for p, h in handles.items()}
                record["automatic_cleanup_verified_at"] = time.monotonic()
                assert all(s["exited"] for s in record["automatic_exit"].values())
                assert job.accounting()["active_processes"] == 0
            else:
                stop.wait(8)
        elif mode == "timeout":
            killed = messages.get("subprocess_kill", 9)
            record["fault_at"] = killed["at"]
            try:
                record["result"] = messages.get("result", 3)
            except queue.Empty:
                record["result_missing_3s_after_kill"] = True
        else:
            record["result"] = messages.get("result")
            assert record["result"]["outcome"] == {"answer": "OK"}
        record["observed_at"] = time.monotonic()
        record["native_exit_status"] = {p: exit_status(h, 0) for p, h in handles.items()}
        record["remaining_tree"] = snapshot(set(job.pids()))
        record["native_job"] = job.accounting()
        if "result" in record:
            stdin.write("exit\n")
            stdin.flush()
            record["orderly_caller_exit"] = exit_status(handles[ready["pid"]], 3000)
            record["orderly_tree_exit"] = {p: exit_status(h, 2000) for p, h in handles.items()}
            record["orderly_job"] = job.accounting()
    except BaseException as exc:
        record["experiment_error"] = repr(exc)
    finally:
        record["cleanup_started"] = time.monotonic()
        job.kill()
        record["cleanup_exit_status"] = {p: exit_status(h, 2000) for p, h in handles.items()}
        record["cleanup_job"] = job.accounting()
        stop.set()
        release.set()
        timer.cancel()
        timer.join(1)
        record["watchdog_fired"] = watchdog_fired.is_set()
        if stdin:
            stdin.close()
        if messages:
            messages.thread.join(2)
            record["pipe_reader_stopped"] = not messages.thread.is_alive()
            record["caller_events"] = messages.events
        if stdout and (not messages or not messages.thread.is_alive()):
            stdout.close()
        for handle in handles.values():
            close(handle)
        job.close()
        caller_directory.cleanup()
        server.shutdown()
        server.server_close()
        server_thread.join(2)
        record["cleanup_finished"] = time.monotonic()
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--caller", action="store_true")
    parser.add_argument("--url")
    parser.add_argument("--cwd")
    parser.add_argument("--fixed", action="store_true")
    parser.add_argument("--binding-failure", action="store_true")
    parser.add_argument("--timeout", type=float, default=6)
    parser.add_argument("--output", type=Path, default=SCRIPT.parent / "evidence/provider-failure-isolation/result.json")
    args = parser.parse_args()
    if args.caller:
        caller(args)
        return
    assert os.name == "nt"
    sources = [ROOT / "backend/app/services/llm/provider.py", ROOT / "backend/app/services/knowledge/windows_job.py",
               ROOT / "backend/app/services/llm/process.py", ROOT / "backend/app/services/knowledge/index_worker.py"]
    data = {"python": sys.version, "platform": platform.platform(), "executable": sys.executable,
            "source_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
            "containment": "Observer-held Job; killed only after native outcome observation or 20s watchdog",
            "scenarios": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    modes = ("normal", "timeout", "parent_crash", "binding_failure") if args.fixed else (
        "normal", "timeout", "parent_crash")
    for mode in modes:
        result = experiment(mode, args.fixed)
        data["scenarios"].append(result)
        args.output.write_text(json.dumps(data, indent=2), encoding="utf-8")
        emit(scenario=mode, error=result.get("experiment_error"), native_job=result.get("native_job"),
             cleanup=result.get("cleanup_job"))
    if any(r.get("experiment_error") or r["cleanup_job"]["active_processes"] or
           not r["pipe_reader_stopped"] for r in data["scenarios"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
