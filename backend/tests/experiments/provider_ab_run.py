"""Reuse frozen TCP/PG/D1 fixture; select a benchmark-only API bootstrap."""
import argparse
from pathlib import Path
import sys

import rag_c1_preflight as fixture
import rag_c1_measure
from provider_ab_measure import measure
from provider_ab_hooks import install, replace_once


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--measure-gate', type=Path)
    parser.add_argument('--api', action='store_true')
    args = parser.parse_args()
    out = args.output.resolve()
    if args.api:
        sys.path.insert(0, str(fixture.BACKEND))
        install(out)
        fixture.api(out)
    else:
        source = replace_once(Path(fixture.__file__).read_text(),
            "[sys.executable, '-B', str(Path(__file__).resolve()),",
            "[sys.executable, '-B', "+repr(str(Path(__file__).resolve()))+",")
        namespace = dict(__name__='profiling_fixture', __file__=fixture.__file__)
        exec(compile(source, fixture.__file__, 'exec'), namespace)
        original_environment = namespace['environment']

        def environment(*values):
            result = original_environment(*values)
            result['SESSION_CHAT_CONCURRENCY_ENABLED'] = 'true'
            return result

        namespace['environment'] = environment
        rag_c1_measure.measure = measure
        namespace['main'](out, args.measure_gate.resolve())
