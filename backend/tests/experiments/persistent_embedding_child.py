"""Experimental JSON-lines worker; single task at a time, no restart or retry."""
import argparse
import ctypes
import importlib.util
import json
import os
import sys
import threading
import time
from ctypes import wintypes


def memory():
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (n, ctypes.c_size_t) for n in ("PeakWorkingSetSize", "WorkingSetSize",
            "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
            "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    get_process = ctypes.windll.kernel32.GetCurrentProcess
    get_process.restype = wintypes.HANDLE
    get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
    get_memory.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    if not get_memory(get_process(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError()
    return {"rss": counters.WorkingSetSize, "peak_rss": counters.PeakWorkingSetSize,
            "private_bytes": counters.PrivateUsage, "peak_pagefile": counters.PeakPagefileUsage}


def emit(value):
    print(json.dumps(value), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--cache", required=True)
    args = parser.parse_args()
    start = time.perf_counter()
    spec = importlib.util.spec_from_file_location("original_index_worker", args.source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.limit_memory()  # Same fail-closed 1536 MiB Windows Job limit.
    before = memory()
    imported = time.perf_counter()
    from fastembed import TextEmbedding
    loaded = time.perf_counter()
    encoder = TextEmbedding(model_name=args.model, cache_dir=args.cache, threads=2, local_files_only=True)
    initialized = time.perf_counter()
    emit({"type": "ready", "pid": os.getpid(), "before_init": before, "after_init": memory(),
        "import_ms": (loaded-imported)*1000, "init_ms": (initialized-loaded)*1000,
        "child_start_to_ready_ms": (initialized-start)*1000, "limit_bytes": 1536*1024*1024})
    for line in sys.stdin:
        task = json.loads(line)
        task_id = task["task_id"]
        if task.get("op") == "stop":
            emit({"type": "stopped", "task_id": task_id})
            return
        if task.get("op") == "hold":
            emit({"type": "accepted", "task_id": task_id})
            threading.Event().wait(30)  # Test-only in-flight crash checkpoint.
            raise TimeoutError("Unreleased crash-test checkpoint")
        begin = time.perf_counter()
        # Same production query embedding calculation and materialization.
        vectors = [v.tolist() for v in encoder.query_embed(task["texts"], batch_size=16)]
        result = {"type": "result", "task_id": task_id, "vectors": vectors,
                  "compute_ms": (time.perf_counter()-begin)*1000}
        if task.get("sample_memory"):
            result["memory"] = memory()
        emit(result)


if __name__ == "__main__":
    main()
