"""Fail-closed Windows ownership for one disposable Provider invocation."""
import os
import queue
import subprocess
import threading
import time

# Unconfirmed cleanup retains ownership and disables this execution path until restart.
# This is a failure latch, not a concurrency limiter or request queue.
_lock = threading.Lock()
_quarantine = []
CLEANUP_SECONDS = 5


def run_provider(command, *, input, env, timeout):
    if os.name != "nt":
        raise OSError("Provider process-tree isolation requires Windows Job Objects")
    from app.services.knowledge import windows_job as native

    with _lock:
        if _quarantine:
            raise OSError("Provider cleanup unconfirmed; process restart required")
    deadline = time.monotonic() + timeout
    job = native.Job()
    handles = {}
    stdin = stdout = thread = None

    def capture():
        for pid in job.pids():
            if pid in handles:
                continue
            try:
                handle = native.process_handle(pid)
            except OSError as exc:
                if getattr(exc, "winerror", None) == 87:
                    continue  # Exited before OpenProcess; accounting still checked below.
                raise
            try:
                belongs = job.contains(handle)
            except BaseException:
                native.close(handle)
                raise
            if belongs:
                handles[pid] = handle
            else:
                native.close(handle)  # PID reused outside this Job.

    try:
        # JOB_LIST is installed in CreateProcess, never an assign-after-start fallback.
        launcher, pid, stdin, stdout = job.launch(command, env)
        handles[pid] = launcher
        mailbox = queue.Queue(maxsize=1)

        def communicate():
            try:
                stdin.write(input)
                stdin.close()  # Child reads stdin until EOF.
                value = stdout.read(1024 * 1024 + 1)
                if len(value) > 1024 * 1024:
                    raise OSError("Provider response envelope too large")
                mailbox.put(value)
            except Exception as exc:
                mailbox.put(exc)

        thread = threading.Thread(target=communicate, daemon=True, name="provider-ipc")
        thread.start()
        try:
            output = mailbox.get(timeout=max(.001, deadline - time.monotonic()))
        except queue.Empty:
            raise subprocess.TimeoutExpired(command, timeout) from None
        if isinstance(output, Exception):
            raise OSError("Provider IPC failed") from output
        status = native.exit_status(launcher, max(0, int((deadline-time.monotonic()) * 1000)))
        if not status["exited"]:
            raise subprocess.TimeoutExpired(command, timeout)
        return subprocess.CompletedProcess(command, status["exit_code"], output, "")
    except RuntimeError as exc:
        raise OSError("Provider process isolation could not be established") from exc
    finally:
        # One shared cleanup deadline, including IPC; no unbounded second communicate.
        cleanup_deadline = time.monotonic() + CLEANUP_SECONDS
        try:
            capture()
            job.kill()
            for handle in handles.values():
                if not native.exit_status(handle, max(0, int(
                        (cleanup_deadline-time.monotonic()) * 1000)))["exited"]:
                    raise OSError("Provider process exit unconfirmed")
            while job.accounting()["active_processes"]:
                remaining = cleanup_deadline - time.monotonic()
                if remaining <= 0:
                    raise OSError("Provider Job exit unconfirmed")
                threading.Event().wait(min(.01, remaining))
            if thread:
                thread.join(max(0, cleanup_deadline-time.monotonic()))
                if thread.is_alive():
                    raise OSError("Provider IPC exit unconfirmed")
            for stream in (stdin, stdout):
                if stream:
                    try:
                        stream.close()
                    except OSError:
                        if not stream.closed:
                            raise
            for pid in list(handles):
                native.check(native.close(handles[pid]))
                del handles[pid]
            job.close()
        except BaseException as exc:
            try:
                job.kill()  # Best effort even if member enumeration/confirmation failed.
            except Exception:
                pass
            with _lock:
                _quarantine.append((job, handles, stdin, stdout, thread))
            raise OSError("Provider cleanup unconfirmed; execution disabled until restart") from exc
