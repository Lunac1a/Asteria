"""C2 only, using unchanged C1 preparation, API observer and cleanup."""
import argparse
from pathlib import Path

import rag_c1_measure
import rag_c1_preflight
from rag_c2_measure import measure


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--measure-gate', type=Path, required=True)
    args = parser.parse_args()
    rag_c1_measure.measure = measure
    rag_c1_preflight.main(args.output.resolve(), args.measure_gate.resolve())
