"""Windows read-only process accounting, used only by the benchmark fixture."""
import ctypes as c
from ctypes import wintypes as w
import os
import threading
import time


def sample(pid):
    from app.services.knowledge import windows_job as native

    class Memory(c.Structure):
        _fields_ = [('cb', w.DWORD), ('faults', w.DWORD)] + [(name, c.c_size_t) for name in (
            'peak_rss', 'rss', 'peak_paged', 'paged', 'peak_nonpaged', 'nonpaged',
            'pagefile', 'peak_pagefile', 'private')]
    handle = native.process_handle(pid)
    try:
        value = Memory()
        value.cb = c.sizeof(value)
        fn = c.WinDLL('psapi', use_last_error=True).GetProcessMemoryInfo
        fn.argtypes = [w.HANDLE, c.POINTER(Memory), w.DWORD]
        native.check(fn(handle, c.byref(value), value.cb))
        creation, exit_time, kernel, user = (c.c_uint64() for _ in range(4))
        native.check(native.bind('GetProcessTimes', [w.HANDLE] + [c.POINTER(c.c_uint64)] * 4)(
            handle, c.byref(creation), c.byref(exit_time), c.byref(kernel), c.byref(user)))
        return dict(pid=pid, rss=value.rss, private_bytes=value.private,
                    cpu_seconds=(kernel.value+user.value)/10**7)
    finally:
        native.close(handle)


def watch(app, stop, rows):
    while not stop.is_set():
        row = dict(at=time.monotonic(), groups={})
        try:
            row['groups']['api'] = [sample(os.getpid())]
            worker = app.state.query_worker
            row['groups']['worker'] = [sample(pid) for pid in worker.worker.job.pids()]
            row['groups']['provider'] = [sample(pid) for job in list(app.state.benchmark_jobs)
                                         if getattr(job, 'benchmark_kind', '') == 'provider.py'
                                         and job.handle for pid in job.pids()]
        except Exception as error:
            row['error'] = type(error).__name__
        rows.append(row)
        stop.wait(1)


def start(app):
    stop, rows = threading.Event(), []
    thread = threading.Thread(target=watch, args=(app, stop, rows), daemon=True)
    thread.start()
    return stop, thread, rows
