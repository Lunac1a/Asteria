"""Profile disposable production worker via an instrumented temporary source copy.

No database required: directly exercise the actual retrieval.run_worker boundary.
Alternating paired original/profiled invocations, fresh child per invocation.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import tempfile
import time
import traceback
from unittest.mock import patch

import workspace_creation_concurrency as f
from app.core.config import settings
from app.services.knowledge import retrieval

QUERY = "According to the document, what does a database transaction guarantee?"


def distribution(values):
    values = sorted(values)
    return {"n": len(values), "mean_ms": statistics.mean(values),
        "p50_ms": values[math.ceil(len(values) * .5) - 1],
        "p95_ms": values[math.ceil(len(values) * .95) - 1],
        "min_ms": min(values), "max_ms": max(values)}


def instrument(source):
    def replace(old, new):
        nonlocal source
        assert source.count(old) == 1, f"Unexpected worker source: {old}"
        source = source.replace(old, new)

    source = ('import time as _profile_time\n_profile_events = {}\n'
              'def _stamp(name):\n    _profile_events[name] = _profile_time.perf_counter_ns()\n'
              '_stamp("child_entry")\n' + source)
    replace("def limit_memory():", '_stamp("standard_imports_end")\n\ndef limit_memory():')
    replace("    from fastembed import TextEmbedding", '    _stamp("fastembed_import_start")\n    from fastembed import TextEmbedding\n    _stamp("fastembed_import_end")')
    replace("    encoder = TextEmbedding(", '    _stamp("model_init_start")\n    encoder = TextEmbedding(')
    replace("    method = encoder.query_embed", '    _stamp("model_init_end")\n    method = encoder.query_embed')
    replace("    return [vector.tolist() for vector in method(texts, batch_size=16)]",
        '    _stamp("embedding_start")\n    result = [vector.tolist() for vector in method(texts, batch_size=16)]\n'
        '    _stamp("embedding_end")\n    return result')
    replace("    limit_memory()", '    _stamp("resource_limit_start")\n    limit_memory()\n    _stamp("resource_limit_end")')
    replace("    payload = json.loads(sys.stdin.read())", '    _stamp("input_start")\n    payload = json.loads(sys.stdin.read())\n    _stamp("input_end")')
    replace("        print(json.dumps(main()))", '        _result = main()\n        _stamp("serialize_start")\n'
        '        _encoded = json.dumps(_result)\n        _stamp("serialize_end")\n'
        '        _stamp("stdout_write_start")\n        print(_encoded)\n        _stamp("stdout_write_end")\n'
        '        print("ASTERIA_PROFILE=" + json.dumps(_profile_events), file=sys.stderr)')
    return source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--pairs", type=int, default=30)
    args = parser.parse_args()
    assert args.pairs >= 10
    args.output.mkdir(parents=True, exist_ok=False)
    worker_path = Path(retrieval.__file__).with_name("index_worker.py")
    source = worker_path.read_text(encoding="utf-8")
    evidence = {"query": QUERY, "pairs": args.pairs, "warmup_pairs": 3, "samples": [], "passed": False,
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "source_sha256": hashlib.sha256(worker_path.read_bytes()).hexdigest(),
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
            "cpu": platform.processor(), "clock": str(time.get_clock_info("perf_counter"))}}
    real_run, real_loads, real_dumps = subprocess.run, json.loads, json.dumps
    current = {}
    expected = None

    def run(command, **kwargs):
        assert Path(command[1]).resolve() == worker_path.resolve()
        assert kwargs["env"]["HF_HUB_OFFLINE"] == "1"
        command = list(command)
        if current["mode"] == "profiled":
            command[1] = str(profiled_path)
        current["parent_before_run"] = time.perf_counter_ns()
        result = real_run(command, **kwargs)
        current["parent_after_run"] = time.perf_counter_ns()
        current["returncode"] = result.returncode
        assert result.returncode == 0, result.stderr
        current["stdout"] = result.stdout
        if current["mode"] == "profiled":
            markers = [s for s in result.stderr.splitlines() if s.startswith("ASTERIA_PROFILE=")]
            assert len(markers) == 1
            current["child_events"] = real_loads(markers[0].split("=", 1)[1])
        current["stderr_other"] = [s for s in result.stderr.splitlines() if not s.startswith("ASTERIA_PROFILE=")]
        return result

    def loads(value, *a, **kw):
        begin = time.perf_counter_ns()
        result = real_loads(value, *a, **kw)
        if value == current.get("stdout"):
            current["parent_decode_ns"] = time.perf_counter_ns() - begin
        return result

    def dumps(value, *a, **kw):
        begin = time.perf_counter_ns()
        result = real_dumps(value, *a, **kw)
        if isinstance(value, dict) and "texts" in value and "cache" in value:
            current["parent_encode_ns"] = time.perf_counter_ns() - begin
        return result

    try:
        temp_parent = f.BACKEND.parent / ".local"
        with tempfile.TemporaryDirectory(dir=temp_parent, prefix="embedding-profile-") as folder:
            assert Path(folder).resolve().is_relative_to(temp_parent.resolve())
            cache = f.BACKEND.parent / "data/mvp-test/models/models--Qdrant--paraphrase-multilingual-MiniLM-L12-v2-onnx-Q"
            shutil.copytree(cache, Path(folder) / "models" / cache.name)
            evidence["model_hashes"] = {str(p.relative_to(cache)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in cache.rglob("*") if p.is_file() and p.suffix in (".onnx", ".json")}
            profiled_path = Path(folder) / "profiled_index_worker.py"
            profiled_source = instrument(source)
            profiled_path.write_text(profiled_source, encoding="utf-8")
            (args.output / "instrumented_worker.txt").write_text(profiled_source, encoding="utf-8")
            evidence["model"] = settings.EMBEDDING_MODEL
            with patch.object(settings, "LOCAL_DATA_DIR", folder), patch.object(settings, "EMBEDDING_BACKEND", "fastembed"), \
                    patch.object(retrieval.subprocess, "run", run), patch.object(json, "loads", loads), patch.object(json, "dumps", dumps):
                for pair in range(args.pairs + 3):
                    modes = ["original", "profiled"] if pair % 2 == 0 else ["profiled", "original"]
                    for mode in modes:
                        current = {"pair": pair, "mode": mode, "warmup": pair < 3}
                        evidence["samples"].append(current)
                        begin = time.perf_counter_ns()
                        output = retrieval.run_worker({"texts": [QUERY]}, 30)
                        current["parent_total_ns"] = time.perf_counter_ns() - begin
                        vector = output["vectors"][0]
                        assert len(output["vectors"]) == 1 and len(vector) == 384
                        assert all(math.isfinite(v) for v in vector)
                        if expected is None:
                            expected = vector
                            evidence["reference_vector"] = vector
                        difference = max(abs(a - b) for a, b in zip(vector, expected))
                        assert difference <= 1e-6
                        current["max_abs_difference"] = difference
                        current["vector_norm"] = math.sqrt(sum(v*v for v in vector))
                        assert current["vector_norm"] > 0
                        current.pop("stdout")
                        current["ms"] = {"worker_parent_total": current["parent_total_ns"] / 1e6,
                            "subprocess_run": (current["parent_after_run"] - current["parent_before_run"]) / 1e6,
                            "parent_json_encode": current["parent_encode_ns"] / 1e6,
                            "parent_json_decode": current["parent_decode_ns"] / 1e6}
                        if mode == "profiled":
                            t = current["child_events"]
                            for name, start, end in (("standard_imports", "child_entry", "standard_imports_end"),
                                ("resource_limit", "resource_limit_start", "resource_limit_end"),
                                ("stdin_read_json_decode", "input_start", "input_end"),
                                ("fastembed_import", "fastembed_import_start", "fastembed_import_end"),
                                ("model_init", "model_init_start", "model_init_end"),
                                ("embedding_pipeline", "embedding_start", "embedding_end"),
                                ("post_embedding_return_output_assembly_combined", "embedding_end", "serialize_start"),
                                ("result_json_encode", "serialize_start", "serialize_end"),
                                ("stdout_buffered_write", "stdout_write_start", "stdout_write_end")):
                                current["ms"][name] = (t[end] - t[start]) / 1e6
                            current["ms"]["launch_to_first_marker_combined"] = (t["child_entry"] - current["parent_before_run"]) / 1e6
                            current["ms"]["after_write_to_parent_return_combined"] = (current["parent_after_run"] - t["stdout_write_end"]) / 1e6
                    if pair % 5 == 2:
                        print(f"Completed pair {pair + 1}/{args.pairs + 3}", flush=True)
            measured = [s for s in evidence["samples"] if not s["warmup"]]
            evidence["summary"] = {mode: {key: distribution([s["ms"][key] for s in measured if s["mode"] == mode])
                for key in next(s["ms"] for s in measured if s["mode"] == mode)} for mode in ("original", "profiled")}
            differences = [next(s["ms"]["worker_parent_total"] for s in measured if s["pair"] == p and s["mode"] == "profiled") -
                next(s["ms"]["worker_parent_total"] for s in measured if s["pair"] == p and s["mode"] == "original") for p in range(3, args.pairs + 3)]
            evidence["paired_profile_minus_original_ms"] = differences
            evidence["paired_difference_summary"] = distribution(differences)
            evidence["passed"] = True
        evidence["temporary_cache_removed"] = not Path(folder).exists()
    except BaseException:
        evidence["error"] = traceback.format_exc()
        raise
    finally:
        (args.output / "result.json").write_text(real_dumps(evidence, indent=2), encoding="utf-8")
    print(real_dumps(evidence["summary"]))


if __name__ == "__main__":
    main()
