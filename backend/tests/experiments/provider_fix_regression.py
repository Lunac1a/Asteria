"""Full regression with explicitly isolated recovery PostgreSQL; always clean it."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
OUT = HERE / "evidence/provider-isolation-fix/postgres-regression-verified"


def main():
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUT)
    OUT = parser.parse_args().output.resolve()
    OUT.mkdir(parents=True, exist_ok=False)
    root = "/tmp/asteria-concurrency-cross-provider-" + uuid.uuid4().hex
    linux = "/mnt/" + HERE.drive[0].lower() + HERE.as_posix()[2:]
    wsl = ["wsl", "-d", "Ubuntu-24.04", "--"]
    evidence = {"cluster": root}
    try:
        setup = subprocess.run(wsl + ["sh", linux + "/workspace_concurrency_pg.sh", root, "start"],
                               capture_output=True, text=True, timeout=120)
        (OUT / "setup.log").write_text(setup.stdout + setup.stderr, encoding="utf-8")
        setup.check_returncode()
        subprocess.run(wsl + ["env", "LD_LIBRARY_PATH=" + root + "/runtime/usr/lib/x86_64-linux-gnu",
                       root + "/runtime/usr/lib/postgresql/16/bin/createdb", "-h", "127.0.0.1",
                       "-p", "55489", "-U", "concurrency_test", "recovery_protocol"],
                       check=True, timeout=15)
        env = dict(os.environ, ASTERIA_RECOVERY_TEST_URL=
                   "postgresql+psycopg://concurrency_test@127.0.0.1:55489/recovery_protocol",
                   ASTERIA_RECOVERY_TEST_PGDATA=root + "/pgdata")
        print("Isolated cluster ready; running backend regressions", flush=True)
        with (OUT / "tests.log").open("w", encoding="utf-8") as log:
            result = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"],
                                    cwd=BACKEND, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=300)
        evidence["tests_returncode"] = result.returncode
        result.check_returncode()
    finally:
        cleanup = subprocess.run(wsl + ["sh", linux + "/workspace_cross_cleanup.sh", root],
                                 capture_output=True, text=True, timeout=25)
        evidence.update(cleanup_returncode=cleanup.returncode, cleanup=cleanup.stdout + cleanup.stderr)
        (OUT / "result.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(json.dumps(evidence), flush=True)
        cleanup.check_returncode()


if __name__ == "__main__":
    main()
