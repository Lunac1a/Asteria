"""Existing unittest suites with explicit skip accounting for local/CI release gates."""
import argparse
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('suite', choices=['all', 'postgres'])
    args = parser.parse_args()
    os.chdir(ROOT)
    if args.suite == 'postgres':
        if not all(os.environ.get(k) for k in ('ASTERIA_RECOVERY_TEST_URL', 'ASTERIA_RECOVERY_TEST_PGDATA')):
            parser.error('Explicit isolated PostgreSQL URL and PGDATA required; no SQLite fallback')
        suite = unittest.defaultTestLoader.loadTestsFromNames([
            'test_workspace_delete_recovery', 'test_startup_recovery', 'test_schema_release'])
    else:
        suite = unittest.defaultTestLoader.discover('tests')
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = dict(suite=args.suite, discovered=result.testsRun,
                  passed=result.testsRun - len(result.skipped) - len(result.errors) - len(result.failures)
                  - len(result.expectedFailures) - len(result.unexpectedSuccesses),
                  failures=len(result.failures), errors=len(result.errors),
                  expected_failures=len(result.expectedFailures),
                  unexpected_successes=len(result.unexpectedSuccesses),
                  skipped=[{'test': t.id(), 'reason': reason} for t, reason in result.skipped])
    # In the PG lane only the explicitly SQLite-only negative test may skip.
    unexpected_skip = args.suite == 'postgres' and any(
        t.id() != 'test_startup_recovery.StartupRecoveryTests.test_postgresql_required'
        for t, _ in result.skipped)
    report['accepted'] = result.wasSuccessful() and result.testsRun > 0 and not unexpected_skip
    rendered = json.dumps(report, indent=2)
    print(rendered)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
            stream.write('### Backend test accounting\n```json\n' + rendered + '\n```\n')
    return 0 if report['accepted'] else 1


if __name__ == '__main__':
    sys.exit(main())
