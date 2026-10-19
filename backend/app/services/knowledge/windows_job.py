"""Windows 10+ process-tree ownership. Imported only when explicitly enabled.

JOB_LIST assigns the child atomically at creation; no unprotected fallback.
The non-inherited last Job handle owns all descendants, including venv launchers.
"""
import ctypes as c
from ctypes import wintypes as w
import msvcrt
import os
import subprocess

api = c.WinDLL("kernel32", use_last_error=True)


def bind(name, args, result=w.BOOL):
    fn = getattr(api, name)
    fn.argtypes, fn.restype = args, result
    return fn


close = bind("CloseHandle", [w.HANDLE])
wait = bind("WaitForSingleObject", [w.HANDLE, w.DWORD], w.DWORD)
terminate = bind("TerminateProcess", [w.HANDLE, w.UINT])
open_process = bind("OpenProcess", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE)


def check(value):
    if not value:
        raise c.WinError(c.get_last_error())
    return value


def process_handle(pid):
    return check(open_process(0x100000 | 0x1000 | 1, False, pid))


def exit_status(handle, timeout_ms=5000):
    status = wait(handle, timeout_ms)
    code = w.DWORD()
    check(bind("GetExitCodeProcess", [w.HANDLE, c.POINTER(w.DWORD)])(handle, c.byref(code)))
    return {"wait_result": status, "exited": status == 0, "exit_code": code.value}


class Basic(c.Structure):
    _fields_ = [("process_time", c.c_int64), ("job_time", c.c_int64),
               ("flags", w.DWORD), ("min_ws", c.c_size_t), ("max_ws", c.c_size_t),
               ("active", w.DWORD), ("affinity", c.c_size_t),
               ("priority", w.DWORD), ("scheduling", w.DWORD)]


class Extended(c.Structure):
    _fields_ = [("basic", Basic), ("io", c.c_uint64 * 6),
               ("process_memory", c.c_size_t), ("job_memory", c.c_size_t),
               ("peak_process", c.c_size_t), ("peak_job", c.c_size_t)]


class Accounting(c.Structure):
    _fields_ = [(name, c.c_int64) for name in ("user", "kernel", "period_user", "period_kernel")] + [
        (name, w.DWORD) for name in ("faults", "total", "active", "terminated")]


class Startup(c.Structure):
    _fields_ = [("cb", w.DWORD), ("reserved", w.LPWSTR), ("desktop", w.LPWSTR),
               ("title", w.LPWSTR)] + [(name, w.DWORD) for name in
               ("x", "y", "xsize", "ysize", "xchars", "ychars", "fill", "flags")] + [
               ("show", w.WORD), ("reserved_size", w.WORD), ("reserved_ptr", c.c_void_p),
               ("stdin", w.HANDLE), ("stdout", w.HANDLE), ("stderr", w.HANDLE)]


class StartupEx(c.Structure):
    _fields_ = [("base", Startup), ("attributes", c.c_void_p)]


class ProcessInfo(c.Structure):
    _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE),
               ("pid", w.DWORD), ("tid", w.DWORD)]


class Job:
    def __init__(self):
        self.handle = check(bind("CreateJobObjectW", [c.c_void_p, w.LPCWSTR], w.HANDLE)(None, None))
        try:
            info = Extended()
            info.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE; no breakaway flags.
            check(bind("SetInformationJobObject", [w.HANDLE, c.c_int, c.c_void_p, w.DWORD])(
                self.handle, 9, c.byref(info), c.sizeof(info)))
            flags = w.DWORD()
            check(bind("GetHandleInformation", [w.HANDLE, c.POINTER(w.DWORD)])(self.handle, c.byref(flags)))
            if flags.value & 1:
                raise RuntimeError("Worker Job handle must not be inherited")
        except BaseException:
            self.close()
            raise

    def contains(self, process):
        value = w.BOOL()
        check(bind("IsProcessInJob", [w.HANDLE, w.HANDLE, c.POINTER(w.BOOL)])(
            process, self.handle, c.byref(value)))
        return bool(value.value)

    def accounting(self):
        value = Accounting()
        check(bind("QueryInformationJobObject", [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p])(
            self.handle, 1, c.byref(value), c.sizeof(value), None))
        return {"total_processes": value.total, "active_processes": value.active,
                "terminated_processes": value.terminated}

    def kill(self):
        check(bind("TerminateJobObject", [w.HANDLE, w.UINT])(self.handle, 91))

    def pids(self):
        class Pids(c.Structure):
            _fields_ = [("assigned", w.DWORD), ("count", w.DWORD), ("pids", c.c_size_t * 64)]
        value = Pids()
        check(bind("QueryInformationJobObject", [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p])(
            self.handle, 3, c.byref(value), c.sizeof(value), None))
        if value.count > 64:
            raise RuntimeError("Worker process tree exceeds diagnostic capacity")
        return list(value.pids[:value.count])

    def close(self):
        if self.handle:
            check(close(self.handle))
            self.handle = None

    def launch(self, command, env):
        """Atomically assign via JOB_LIST. Fail closed on unsupported Windows/jobs."""
        read_in, write_in = os.pipe()
        read_out, write_out = os.pipe()
        error = open(os.devnull, "wb")  # Never accumulate unbounded model diagnostics.
        fds = [read_in, write_in, read_out, write_out]
        attributes = None
        initialized = False
        info = ProcessInfo()
        try:
            child_fds = [read_in, write_out, error.fileno()]
            for fd in child_fds:
                os.set_inheritable(fd, True)
            inherited = (w.HANDLE * 3)(*(msvcrt.get_osfhandle(fd) for fd in child_fds))
            jobs = (w.HANDLE * 1)(self.handle)
            size = c.c_size_t()
            init = bind("InitializeProcThreadAttributeList", [c.c_void_p, w.DWORD, w.DWORD, c.POINTER(c.c_size_t)])
            init(None, 2, 0, c.byref(size))
            attributes = c.create_string_buffer(size.value)
            check(init(attributes, 2, 0, c.byref(size)))
            initialized = True
            update = bind("UpdateProcThreadAttribute", [c.c_void_p, w.DWORD, c.c_size_t,
                          c.c_void_p, c.c_size_t, c.c_void_p, c.c_void_p])
            check(update(attributes, 0, 0x20002, inherited, c.sizeof(inherited), None, None))
            check(update(attributes, 0, 0x2000D, jobs, c.sizeof(jobs), None, None))
            startup = StartupEx()
            startup.base.cb = c.sizeof(startup)
            startup.base.flags = 0x100  # STARTF_USESTDHANDLES
            startup.base.stdin, startup.base.stdout, startup.base.stderr = inherited
            startup.attributes = c.cast(attributes, c.c_void_p)
            env_block = c.create_unicode_buffer("\0".join(f"{k}={v}" for k, v in sorted(env.items())) + "\0\0")
            create = bind("CreateProcessW", [w.LPCWSTR, w.LPWSTR, c.c_void_p, c.c_void_p,
                          w.BOOL, w.DWORD, c.c_void_p, w.LPCWSTR, c.c_void_p, c.POINTER(ProcessInfo)])
            check(create(command[0], c.create_unicode_buffer(subprocess.list2cmdline(command)),
                         None, None, True, 0x08000000 | 0x00080000 | 0x400,
                         env_block, None, c.byref(startup), c.byref(info)))
            check(close(info.thread))
            if not self.contains(info.process):
                raise RuntimeError("Worker was not assigned to its Job")
            stdin = os.fdopen(write_in, "w", encoding="utf-8", buffering=1)
            fds.remove(write_in)
            stdout = os.fdopen(read_out, "r", encoding="utf-8")
            fds.remove(read_out)
            return info.process, info.pid, stdin, stdout
        except BaseException:
            if info.process:
                self.kill()
                wait(info.process, 5000)
                close(info.process)
            raise
        finally:
            if initialized:
                bind("DeleteProcThreadAttributeList", [c.c_void_p], None)(attributes)
            for fd in fds:
                os.close(fd)
            error.close()
