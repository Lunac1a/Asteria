"""Standalone persistent query embedder. No application settings or credentials."""
import argparse
import json
import os
import sys

from index_worker import limit_memory


def emit(message):
    print(json.dumps(message, allow_nan=False), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--cache", required=True)
    args = parser.parse_args()
    limit_memory()
    from fastembed import TextEmbedding

    encoder = TextEmbedding(model_name=args.model, cache_dir=args.cache,
                            threads=2, local_files_only=True)
    emit({"type": "ready", "pid": os.getpid(), "model": args.model})
    while line := sys.stdin.readline(65537):
        if len(line) > 65536 or not line.endswith("\n"):
            raise ValueError("Invalid request frame")
        task = json.loads(line)
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or len(task_id) != 32:
            raise ValueError("Invalid task identity")
        if task.get("op") == "stop":
            emit({"type": "stopped", "task_id": task_id})
            return
        texts = task.get("texts")
        if (task.get("op") != "embed" or not isinstance(texts, list) or len(texts) != 1
                or not isinstance(texts[0], str) or not 0 < len(texts[0]) <= 16000):
            raise ValueError("Invalid query")
        vectors = [vector.tolist() for vector in encoder.query_embed(texts, batch_size=16)]
        emit({"type": "result", "task_id": task_id, "vectors": vectors})


if __name__ == "__main__":
    main()
