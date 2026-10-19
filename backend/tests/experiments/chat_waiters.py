"""Run deterministic registry/route capacity checks and retain JSON evidence."""
import argparse
import json
from pathlib import Path
import platform
import subprocess
import sys
import unittest

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))
import test_chat_waiters as checks  # noqa: E402
import test_chat_singleflight as original  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    loader = unittest.TestLoader()
    suite = unittest.TestSuite([loader.loadTestsFromModule(original), loader.loadTestsFromModule(checks)])
    # Repeat simultaneous admission/duplicate-release contention without sleeps.
    for _ in range(19):
        suite.addTest(checks.BoundedWaiterTests("test_global_atomic_race_and_duplicate_release"))
    with (args.output / "tests.log").open("w", encoding="utf-8") as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    evidence = {"passed": result.wasSuccessful(), "tests": result.testsRun,
                "failures": result.failures, "errors": result.errors,
                "python": platform.python_version(), "platform": platform.platform(),
                "head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "scope": "Deterministic real threads and route fault injection, no HTTP or database in this runner",
                "observations": checks.OBSERVATIONS}
    (args.output / "result.json").write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"passed": evidence["passed"], "tests": evidence["tests"]}))
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
