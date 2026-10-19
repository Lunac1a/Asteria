"""Provision, run, archive and clean a fresh single-worker PostgreSQL experiment."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--experiment", choices=["workspace_creation_concurrency.py", "pool_index_lifecycle.py", "chat_baseline.py"],
                        default="workspace_creation_concurrency.py")
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    here = Path(__file__).resolve().parent
    linux = "/mnt/" + here.drive[0].lower() + here.as_posix()[2:]
    root = "/tmp/asteria-concurrency-cross-single-" + uuid.uuid4().hex
    wsl = ["wsl", "-d", "Ubuntu-24.04", "--"]
    cleanup = None
    try:
        setup = subprocess.run(wsl + ["sh", linux + "/workspace_concurrency_pg.sh", root, "start"],
                               capture_output=True, text=True, timeout=120)
        (out / "setup.log").write_text(setup.stdout + setup.stderr, encoding="utf-8")
        setup.check_returncode()
        result = subprocess.run([sys.executable, "-B", str(here / args.experiment),
            "--url", "postgresql+psycopg://concurrency_test@127.0.0.1:55489/workspace_concurrency_test",
            "--pgdata", root + "/pgdata", "--output", str(out / "result.json")], timeout=args.timeout)
        result.check_returncode()
    finally:
        log = subprocess.run(wsl + ["cat", root + "/postgres.log"], capture_output=True, text=True, timeout=10)
        (out / "postgres.log").write_text(log.stdout + log.stderr, encoding="utf-8")
        cleanup = subprocess.run(wsl + ["sh", linux + "/workspace_cross_cleanup.sh", root],
                                 capture_output=True, text=True, timeout=25)
        (out / "cleanup.json").write_text(json.dumps({"cluster": root,
            "returncode": cleanup.returncode, "output": cleanup.stdout + cleanup.stderr,
            "sql_log_archived": log.returncode == 0}, indent=2), encoding="utf-8")
    cleanup.check_returncode()


if __name__ == "__main__":
    main()
