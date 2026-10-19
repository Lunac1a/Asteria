"""Four balanced single-service TCP/PG benchmark blocks; no production edits.

First create the dedicated cluster with workspace_concurrency_pg.sh. Pass its
exact PGDATA. After this runner, stop/remove it with workspace_cross_cleanup.sh.
The database must be empty. Each child verifies its identity before any writes.
"""
import argparse
import hashlib
import json
from pathlib import Path
import random
import statistics
import subprocess
import sys

from chat_baseline import distribution


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--pgdata", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20261019)
    parser.add_argument("--summarize", action="store_true", help="Revalidate existing blocks without rerunning load")
    args = parser.parse_args()
    if not args.summarize:
        args.output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[3]
    def hashes():
        return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (root / "backend/app").rglob("*.py")}
    before = hashes()
    if args.summarize:
        original = json.loads((args.output / "result.json").read_text(encoding="utf-8"))
        assert before == original["production_hashes_before"]
        before = original["production_hashes_before"]
    first = random.Random(args.seed).choice(["disposable", "persistent"])
    other = "persistent" if first == "disposable" else "disposable"
    order = [first, other, other, first]
    evidence = {"passed": False, "seed": args.seed, "order": order, "blocks": [],
                "production_hashes_before": before,
                "percentiles": "nearest rank; 50 samples per arm, P99 equals maximum",
                "throughput": "achieved includes per-request independent DB verification gaps; HTTP-normalized excludes gaps"}
    try:
        for index, mode in enumerate(order):
            output = args.output / f"{index}-{mode}.json"
            if not args.summarize:
                with output.with_suffix(".log").open("w", encoding="utf-8") as log:
                    result = subprocess.run([sys.executable, "-B", str(Path(__file__).with_name("chat_worker_ab_block.py")),
                        "--url", args.url, "--pgdata", args.pgdata, "--mode", mode, "--count", "25",
                        "--output", str(output)], stdout=log, stderr=subprocess.STDOUT, timeout=240,
                        creationflags=subprocess.CREATE_NO_WINDOW)
                assert result.returncode == 0
            block = json.loads(output.read_text(encoding="utf-8"))
            evidence["blocks"].append(block)
            assert block["passed"] and block["api_stopped"] and block["schema_removed"]
            print(f"Block {index + 1}/4 {mode}: mean HTTP {block['summary']['http']['mean_ms']:.2f} ms", flush=True)
        evidence["arms"] = {}
        all_samples = []
        for mode in ("disposable", "persistent"):
            blocks = [b for b in evidence["blocks"] if b["mode"] == mode]
            samples = [s for b in blocks for s in b["samples"] if not s["warmup"]]
            all_samples.extend(samples)
            assert len(samples) == 50 and all(s["valid"] and s["status"] == 200 for s in samples)
            evidence["arms"][mode] = {
                "latency": {k: distribution([s["ms"][k] for s in samples]) for k in samples[0]["ms"]},
                "success_rate": 1.0,
                "achieved_throughput_requests_s": 50 / sum(b["measured_loop_seconds"] for b in blocks),
                "http_time_normalized_requests_s": 50000 / sum(s["ms"]["http"] for s in samples),
                "block_means_http_ms": [b["summary"]["http"]["mean_ms"] for b in blocks]}
        assert len({s["request_id"] for s in all_samples}) == 100
        assert len({s["session_id"] for s in all_samples}) == 100
        assert len({s["vector_hash"] for s in all_samples}) == 1
        assert len({s["prompt_hash"] for s in all_samples}) == 1
        assert all(s["response"]["session_id"] == s["session_id"] for s in all_samples)
        assert len({json.dumps({k: v for k, v in s["response"].items() if k != "session_id"}, sort_keys=True)
                    for s in all_samples}) == 1
        evidence["correctness"] = "100 unique turns/sessions; identical vectors, prompts and responses excluding unique session_id; independent Message/ChatTurn/Evidence checks for every request"
        evidence["improvement"] = {}
        for metric in ("http", "retrieval", "embedding_worker_nested"):
            a = evidence["arms"]["disposable"]["latency"][metric]["mean_ms"]
            b = evidence["arms"]["persistent"]["latency"][metric]["mean_ms"]
            evidence["improvement"][metric] = {"absolute_ms": a-b, "percent": (a-b)/a*100, "speedup": a/b}
        memory = [b["memory_after_warmup"] for b in evidence["blocks"] if b["mode"] == "persistent"]
        evidence["persistent_extra_job_rss_mib"] = [sum(p["rss_bytes"] for p in m["job_members"])/2**20 for m in memory]
        evidence["persistent_extra_job_rss_mean_mib"] = statistics.mean(evidence["persistent_extra_job_rss_mib"])
        evidence["production_hashes_after"] = hashes()
        assert before == evidence["production_hashes_after"]
        evidence["passed"] = True
    finally:
        (args.output / "result.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
