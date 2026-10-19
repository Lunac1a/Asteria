"""Windows 1/2-call Provider resource experiment. No database or paid provider."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import ctypes as c
from ctypes import wintypes as w
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import threading
import time

import provider_failure_isolation as f
from app.services.knowledge import windows_job as native

SCRIPT = Path(__file__).resolve()


class Memory(c.Structure):
    _fields_ = [("cb", w.DWORD), ("faults", w.DWORD)] + [(name, c.c_size_t) for name in (
        "peak_rss", "rss", "peak_paged", "paged", "peak_nonpaged", "nonpaged",
        "pagefile", "peak_pagefile", "private")]


def metrics(handle):
    value = Memory()
    value.cb = c.sizeof(value)
    fn = c.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
    fn.argtypes = [w.HANDLE, c.POINTER(Memory), w.DWORD]
    native.check(fn(handle, c.byref(value), value.cb))
    creation, exit_time, kernel, user = (c.c_uint64() for _ in range(4))
    native.check(native.bind("GetProcessTimes", [w.HANDLE] + [c.POINTER(c.c_uint64)] * 4)(
        handle, c.byref(creation), c.byref(exit_time), c.byref(kernel), c.byref(user)))
    return {"rss": value.rss, "peak_rss": value.peak_rss, "private_bytes": value.private,
            "cpu_seconds": (kernel.value + user.value) / 10**7}


def caller(args):
    os.chdir(args.cwd)
    os.environ.update(APP_NAME="provider-concurrency", APP_PORT="1", FRONTEND_URL="http://127.0.0.1",
                      DATABASE_URL="postgresql://invalid@127.0.0.1:1/no_database",
                      JWT_SECRET_KEY="synthetic", JWT_ALGORITHM="HS256", JWT_EXPIRE_MINUTES="1",
                      ENCRYPTION_KEY="synthetic", LLM_ALLOWED_BASE_URLS=args.url,
                      RUNTIME_METRICS_ENABLED="false")
    from app.services.llm.provider import generate_response
    from app.services.llm import process
    from app.schemas.llm_settings import validate_provider_url
    validate_provider_url(args.url)
    lock = threading.Lock()
    original_launch, original_close = native.Job.launch, native.Job.close

    def emit(**value):
        with lock:
            f.emit(**value)

    def launch(job, command, env):
        result = original_launch(job, command, env)
        job.test_pid = result[1]
        emit(event="provider_launcher", pid=result[1])
        return result

    def close(job):
        if hasattr(job, "test_pid") and job.handle:
            info = native.Accounting()
            native.check(native.bind("QueryInformationJobObject", [w.HANDLE, c.c_int, c.c_void_p,
                         w.DWORD, c.c_void_p])(job.handle, 1, c.byref(info), c.sizeof(info), None))
            emit(event="provider_job_exit", pid=job.test_pid, active=info.active,
                 total=info.total, cpu_seconds=(info.user + info.kernel) / 10**7)
        return original_close(job)

    native.Job.launch, native.Job.close = launch, close
    emit(event="ready", pid=os.getpid())
    assert sys.stdin.readline().strip() == "go"
    barrier = threading.Barrier(args.count + 1, timeout=8)

    def invoke(index):
        barrier.wait()
        start = time.monotonic()
        try:
            answer = generate_response([{"role": "user", "content": "Return OK"}],
                                       "synthetic-key", args.url, "mock", timeout_seconds=6)
            result = {"answer": str(answer)}
        except Exception as exc:
            result = {"error": type(exc).__name__, "category": getattr(exc, "category", None)}
        emit(event="call_done", index=index, started=start, seconds=time.monotonic()-start, **result)

    with ThreadPoolExecutor(max_workers=args.count) as pool:
        futures = [pool.submit(invoke, i) for i in range(args.count)]
        barrier.wait()
        for future in futures:
            future.result(timeout=15)
    emit(event="batch_done", quarantine=len(process._quarantine),
         python_threads=[t.name for t in threading.enumerate()])
    sys.stdin.readline()


def run_case(count, mode):
    all_entered, release, stop = threading.Event(), threading.Event(), threading.Event()
    request_lock = threading.Lock()
    arrivals = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            with request_lock:
                arrivals.append(time.monotonic())
                if len(arrivals) == count:
                    all_entered.set()
            if not release.wait(10):
                return
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                if mode == "normal":
                    body = json.dumps({"id": "local", "object": "chat.completion", "created": 0,
                                       "model": "mock", "choices": [{"index": 0, "finish_reason": "stop",
                                       "message": {"role": "assistant", "content": "OK"}}]}).encode()
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_header("Content-Length", "1000000")
                    self.end_headers()
                    while not stop.is_set():
                        self.wfile.write(b" ")
                        self.wfile.flush()
                        stop.wait(.2)
            except OSError:
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    job = native.Job()
    handles, samples = {}, []
    sampling_stop, watchdog_fired = threading.Event(), threading.Event()
    record = {"count": count, "mode": mode, "arrivals": arrivals, "samples": samples}
    si = so = messages = sampler = None
    temp = tempfile.TemporaryDirectory(prefix="asteria-provider-concurrency-")

    def sample():
        while not sampling_stop.is_set():
            try:
                pids = job.pids()
                for pid in pids:
                    if pid not in handles:
                        handles[pid] = native.check(native.open_process(0x101011, False, pid))
                rows = f.snapshot(set(pids))
                for row in rows:
                    row.update(metrics(handles[row["pid"]]))
                samples.append({"at": time.monotonic(), "processes": rows})
            except OSError as exc:
                # A process may exit between enumeration and opening/querying its handle.
                samples.append({"at": time.monotonic(), "sampling_race": repr(exc)})
            sampling_stop.wait(.1)

    def watchdog():
        watchdog_fired.set()
        job.kill()

    timer = threading.Timer(22, watchdog)
    timer.start()
    try:
        env = {k: v for k, v in os.environ.items() if k.upper() in {
            "SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA"}}
        launcher, pid, si, so = job.launch([sys.executable, "-B", str(SCRIPT), "--caller",
            "--count", str(count), "--url", f"http://127.0.0.1:{server.server_port}/v1",
            "--cwd", temp.name], env)
        handles[pid] = launcher
        messages = f.Messages(so)
        ready = messages.get("ready")
        record["caller_pid"] = ready["pid"]
        record["baseline_tree"] = f.snapshot(set(job.pids()))
        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()
        si.write("go\n")
        si.flush()
        assert all_entered.wait(5), "Not all calls reached HTTP overlap barrier"
        record["barrier_at"] = time.monotonic()
        # Fixed observation workload after proven overlap; never used to establish ordering.
        stop.wait(2)
        record["release_at"] = time.monotonic()
        release.set()
        if mode == "crash":
            native.check(native.terminate(handles[ready["pid"]], 93))
            record["crash_at"] = time.monotonic()
            deadline = time.monotonic() + 5
            record["automatic_exits"] = {p: native.exit_status(h, max(0, int(
                (deadline-time.monotonic()) * 1000))) for p, h in list(handles.items())}
            record["automatic_exit_at"] = time.monotonic()
            assert all(s["exited"] for s in record["automatic_exits"].values())
            assert job.accounting()["active_processes"] == 0
        else:
            record["batch"] = messages.get("batch_done", 10)
            assert record["batch"]["quarantine"] == 0
            done = [v for v in messages.events if v["event"] == "call_done"]
            assert len(done) == count
            assert all(v.get("answer") == "OK" if mode == "normal" else
                       v.get("category") == "provider_timeout" for v in done)
            si.write("exit\n")
            si.flush()
            deadline = time.monotonic() + 5
            record["orderly_exits"] = {p: native.exit_status(h, max(0, int(
                (deadline-time.monotonic()) * 1000))) for p, h in list(handles.items())}
            assert all(s["exited"] for s in record["orderly_exits"].values())
            assert job.accounting()["active_processes"] == 0
        record["native_job"] = job.accounting()
    except BaseException as exc:
        record["error"] = repr(exc)
    finally:
        sampling_stop.set()
        if sampler:
            sampler.join(2)
        record["cleanup_at"] = time.monotonic()
        job.kill()
        deadline = time.monotonic() + 5
        record["cleanup_exits"] = {p: native.exit_status(h, max(0, int(
            (deadline-time.monotonic()) * 1000))) for p, h in handles.items()}
        record["cleanup_job"] = job.accounting()
        timer.cancel()
        timer.join(1)
        record["watchdog_fired"] = watchdog_fired.is_set()
        stop.set()
        release.set()
        if si:
            si.close()
        if messages:
            messages.thread.join(2)
            record["events"] = messages.events
            record["reader_stopped"] = not messages.thread.is_alive()
        if so and not messages.thread.is_alive():
            so.close()
        for handle in handles.values():
            native.close(handle)
        job.close()
        server.shutdown()
        server.server_close()
        server_thread.join(2)
        temp.cleanup()
    return record


def summarize(case):
    roots = {v["pid"] for v in case["events"] if v["event"] == "provider_launcher"}
    samples = []
    for sample in case["samples"]:
        rows = sample.get("processes", [])
        selected = set(roots)
        for _ in range(4):
            selected.update(p["pid"] for p in rows if p["ppid"] in selected)
        providers = [p for p in rows if p["pid"] in selected]
        if providers:
            samples.append({"at": sample["at"], "processes": len(providers),
                            **{key: sum(p[key] for p in providers) for key in
                               ("rss", "private_bytes", "threads", "handles", "cpu_seconds")}})
    return {"count": case["count"], "mode": case["mode"], "error": case.get("error"),
            "sampled_provider_peaks": {key: max((s[key] for s in samples), default=0) for key in
                                       ("processes", "rss", "private_bytes", "threads", "handles")},
            "job_cpu_seconds": sum(v["cpu_seconds"] for v in case["events"] if v["event"] == "provider_job_exit"),
            "cpu_complete": case["mode"] != "crash", "provider_samples": samples,
            "latencies": [v["seconds"] for v in case["events"] if v["event"] == "call_done"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--caller", action="store_true")
    parser.add_argument("--cwd")
    parser.add_argument("--url")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--output", type=Path, default=SCRIPT.parent / "evidence/provider-concurrency/result.json")
    args = parser.parse_args()
    if args.caller:
        caller(args)
        return
    data = {"python": sys.version, "platform": platform.platform(), "logical_cpus": os.cpu_count(),
            "clock_resolution": time.get_clock_info("monotonic").resolution,
            "source_hashes": {str(p.relative_to(f.ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                              (f.ROOT / "backend/app/services/llm/provider.py", f.ROOT / "backend/app/services/llm/process.py")},
            "cases": [], "summary": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    schedule = [(n, "normal") for n in (1, 2, 2, 1, 1, 2)] + [
        (1, "timeout"), (2, "timeout"), (1, "crash"), (2, "crash")]
    for count, mode in schedule:
        case = run_case(count, mode)
        data["cases"].append(case)
        summary = summarize(case)
        data["summary"].append(summary)
        args.output.write_text(json.dumps(data, indent=2), encoding="utf-8")
        f.emit(count=count, mode=mode, error=case.get("error"), peaks=summary["sampled_provider_peaks"],
               cpu=summary["job_cpu_seconds"], latency=summary["latencies"])
        if case.get("error") or case["cleanup_job"]["active_processes"] or case["watchdog_fired"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
