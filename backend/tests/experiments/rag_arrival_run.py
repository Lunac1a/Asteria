"""Run one fixed-rate I/S0 or I/S1 repetition with the frozen C1 fixture."""
import argparse
from pathlib import Path

import rag_c1_preflight
import rag_c1_measure
from rag_arrival_measure import measure


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--configuration', choices=['S0', 'S1'], required=True)
    parser.add_argument('--measure-gate', type=Path, required=True)
    args = parser.parse_args()
    original = rag_c1_preflight.environment

    def environment(*values):
        result = original(*values)
        result['SESSION_CHAT_CONCURRENCY_ENABLED'] = str(args.configuration == 'S1').lower()
        return result

    rag_c1_preflight.environment = environment
    rag_c1_measure.measure = measure
    rag_c1_preflight.main(args.output.resolve(), args.measure_gate.resolve())
