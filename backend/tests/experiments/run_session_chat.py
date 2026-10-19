"""Own the isolated cluster and experiment process trees, then run full regressions."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
sys.path.insert(0, str(BACKEND))
from app.services.knowledge import windows_job as native  # noqa: E402


def managed(command, out):
    job = native.Job()
    h = si = so = reader = None
    statuses = []
    try:
        h, pid, si, so = job.launch(command, dict(os.environ))
        si.close()
        def read():
            with out.open("w", encoding="utf-8") as log:
                for line in so:
                    log.write(line)
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        status = native.exit_status(h, 240000)
        assert status["exited"] and status["exit_code"] == 0, status
    finally:
        members = [native.process_handle(p) for p in job.pids()]
        job.kill()
        for ph in members:
            statuses.append(native.exit_status(ph, 2000))
            native.close(ph)
        if h:
            statuses.append(native.exit_status(h, 2000))
            native.close(h)
        if reader:
            reader.join(3)
        if so and not reader.is_alive():
            so.close()
        result = {"exits": statuses, "job": job.accounting(), "reader_stopped": not reader.is_alive()}
        out.with_suffix(".cleanup.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        job.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--experiment-only", action="store_true")
    parser.add_argument("--skip-regression", action="store_true")
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    root = "/tmp/asteria-concurrency-cross-session-chat-" + uuid.uuid4().hex
    linux = "/mnt/" + HERE.drive[0].lower() + HERE.as_posix()[2:]
    wsl = ["wsl", "-d", "Ubuntu-24.04", "--"]
    result = {"cluster": root}
    try:
        setup = subprocess.run(wsl + ["sh", linux + "/workspace_concurrency_pg.sh", root, "start"],
                               capture_output=True, text=True, timeout=120)
        (out / "setup.log").write_text(setup.stdout + setup.stderr, encoding="utf-8")
        setup.check_returncode()
        url = "postgresql+psycopg://concurrency_test@127.0.0.1:55489/workspace_concurrency_test"
        print("Isolated PostgreSQL ready; running real two-session experiment", flush=True)
        managed([sys.executable, "-B", str(HERE / "session_chat_concurrency.py"), "--url", url,
                 "--pgdata", root + "/pgdata", "--output", str(out / "http")], out / "http.log")
        result["http"] = "passed"
        if args.experiment_only:
            return
        print("Two-session HTTP passed; checking Learning/Delete/rollback with flag enabled", flush=True)
        managed([sys.executable, "-B", str(HERE / "session_coordination.py"), "--url", url,
                 "--pgdata", root + "/pgdata", "--concurrent", "--output", str(out / "coordination")], out / "coordination.log")
        result["coordination"] = "passed"
        if args.skip_regression:
            return
        subprocess.run(wsl + ["env", "LD_LIBRARY_PATH=" + root + "/runtime/usr/lib/x86_64-linux-gnu",
                       root + "/runtime/usr/lib/postgresql/16/bin/createdb", "-h", "127.0.0.1",
                       "-p", "55489", "-U", "concurrency_test", "recovery_protocol"], check=True, timeout=15)
        env = dict(os.environ, ASTERIA_RECOVERY_TEST_URL=
                   "postgresql+psycopg://concurrency_test@127.0.0.1:55489/recovery_protocol",
                   ASTERIA_RECOVERY_TEST_PGDATA=root + "/pgdata")
        print("Running full Backend regressions", flush=True)
        with (out / "regression.log").open("w", encoding="utf-8") as log:
            tests = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"],
                                   cwd=BACKEND, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=300)
        result["regression_returncode"] = tests.returncode
        tests.check_returncode()
    finally:
        cleanup = subprocess.run(wsl + ["sh", linux + "/workspace_cross_cleanup.sh", root],
                                 capture_output=True, text=True, timeout=25)
        result.update(cleanup_returncode=cleanup.returncode, cleanup=cleanup.stdout + cleanup.stderr)
        (out / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result), flush=True)
        cleanup.check_returncode()


if __name__ == "__main__":
    main()
