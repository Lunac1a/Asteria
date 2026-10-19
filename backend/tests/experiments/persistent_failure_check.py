"""Verify actual model-process exit as well as Windows venv launcher exit."""
import argparse
import json
from pathlib import Path
import shutil
import tempfile
import time
from persistent_embedding_poc import Worker, f, retrieval, settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    result = {"cases": [], "passed": False}
    try:
        with tempfile.TemporaryDirectory(dir=f.BACKEND.parent / ".local", prefix="persistent-failure-") as folder:
            assert Path(folder).resolve().is_relative_to((f.BACKEND.parent / ".local").resolve())
            source_cache = f.BACKEND.parent / "data/mvp-test/models/models--Qdrant--paraphrase-multilingual-MiniLM-L12-v2-onnx-Q"
            shutil.copytree(source_cache, Path(folder) / "models" / source_cache.name)
            for mode in ("graceful", "terminate_actual"):
                worker = Worker(Path(retrieval.__file__).with_name("index_worker.py"), settings.EMBEDDING_MODEL,
                    Path(folder) / "models", args.output / f"{mode}.stderr.log")
                case = {"mode": mode}
                result["cases"].append(case)
                try:
                    case["ready"] = worker.receive(30)
                    assert case["ready"]["type"] == "ready"
                    response = worker.embed()
                    assert len(response["vectors"][0]) == 384
                    case["normal_task_id"] = response["task_id"]
                    if mode == "terminate_actual":
                        task_id = worker.send(op="hold")
                        assert worker.receive(5) == {"type": "accepted", "task_id": task_id}
                        start = time.perf_counter()
                        worker.terminate_actual()
                        try:
                            worker.receive(5)
                            raise AssertionError("Unexpected result from terminated worker")
                        except RuntimeError as exc:
                            case.update(task_id=task_id, detected_error=str(exc),
                                detection_ms=(time.perf_counter()-start)*1000, parent_survived=True)
                        worker.process.wait(5)
                finally:
                    case["cleanup"] = worker.close()
            result["passed"] = True
        result["temporary_files_removed"] = not Path(folder).exists()
    finally:
        (args.output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
