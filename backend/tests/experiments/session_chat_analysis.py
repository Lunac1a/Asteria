"""Derive small-sample controlled HTTP metrics; assert actual LLM interval overlap."""
import argparse
import json
import math
from pathlib import Path
import statistics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path)
    args = parser.parse_args()
    data = json.loads(args.result.read_text(encoding="utf-8"))
    assert data["passed"] and data["api_stopped"]
    summary = {"modes": {}, "overlap": [], "resources": []}
    for enabled in (False, True):
        batches = [b for b in data["batches"] if b["enabled"] == enabled]
        observations, seconds = [], 0
        for batch in batches:
            rows = [r for r in data["http"] if r["label"] in batch["labels"]]
            observations.extend(rows)
            seconds += max(r["end"] for r in rows) - min(r["start"] for r in rows)
            if enabled:
                starts, ends, work_starts = [], [], []
                for key in batch["labels"]:
                    start = next(e for e in data["events"] if e["event"] == "llm_start" and e.get("request") == key)
                    end = next(e for e in data["events"] if e["event"] == "llm_end" and e.get("request") == key)
                    embedding = next(e for e in data["events"] if e["event"] == "embedding_done" and e["label"] == key)
                    assert embedding["at"] < start["at"]
                    assert len(start["history"]) == 2
                    starts.append(start["at"])
                    ends.append(end["at"])
                    work_starts.append(next(e["at"] for e in data["events"] if
                        e["event"] == "llm_work_start" and e.get("request") == key))
                overlap = min(ends) - max(starts)
                assert overlap > 0
                work_overlap = min(ends) - max(work_starts)
                assert work_overlap > 0
                summary["overlap"].append({"batch": batch["index"], "stage_seconds": overlap,
                                           "fixed_work_seconds": work_overlap})
        successful = [r for r in observations if r["status"] == 200]
        def dist(rows):
            values = sorted((r["end"]-r["start"])*1000 for r in rows)
            return {"n": len(values), "mean_ms": statistics.mean(values),
                    "p50_ms": values[math.ceil(len(values)*.5)-1],
                    "p95_ms": values[math.ceil(len(values)*.95)-1]}
        summary["modes"][str(enabled)] = {"http": dist(observations), "successful_http": dist(successful),
            "success_rate": len(successful)/len(observations), "successful_throughput": len(successful)/seconds,
            "429_rate": sum(r["status"] == 429 for r in observations)/len(observations),
            "409_rate": sum(r["status"] == 409 for r in observations)/len(observations), "cohort_seconds": seconds}
    for batch, sample in zip(data["batches"], data["resource_samples"]):
        summary["resources"].append({"enabled": batch["enabled"], "providers": len(sample["providers"]),
            **{k: sum(p[k] for p in sample["providers"]) for k in ("rss", "private_bytes", "threads", "handles", "cpu_seconds")},
            "api_rss": sample["api"]["rss_bytes"],
            "embedding_rss": sum(p["rss_bytes"] for p in sample["embedding"])})
    summary["limits"] = "Three paired batches/mode; event-gated overlap plus fixed2s mock delay; not a capacity benchmark. Resources are held-point snapshots, not peaks."
    args.result.with_name("analysis.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
