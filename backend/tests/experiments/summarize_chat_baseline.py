"""Combine valid baseline runs without dropping outliers or warmup relabeling."""
import argparse
import json
from pathlib import Path
from chat_baseline import distribution


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in args.inputs]
    assert all(r["passed"] and r["api_stopped"] for r in runs)
    assert all(r["workload"] == runs[0]["workload"] for r in runs)
    assert all(r["source_hashes"] == runs[0]["source_hashes"] for r in runs)
    samples = [s for r in runs for s in r["samples"] if not s["warmup"]]
    assert all(s["valid"] and s["status"] == 200 for s in samples)
    assert len({s["request_id"] for s in samples}) == len(samples)
    assert len({s["session_id"] for s in samples}) == len(samples)
    result = {"inputs": [str(p) for p in args.inputs], "measured": len(samples),
        "warmups_excluded": sum(r["warmups"] for r in runs), "success_rate": 1.0,
        "summary": {k: distribution([s["ms"][k] for s in samples]) for k in samples[0]["ms"]},
        "per_run_http": [r["summary"]["http"] for r in runs],
        "per_run_batch_means_ms": [r["batch_means_ms"] for r in runs],
        "percentiles": "nearest rank across all measured samples, no outliers removed"}
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"]["http"]))


if __name__ == "__main__":
    main()
