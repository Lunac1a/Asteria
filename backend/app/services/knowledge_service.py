import json
import math
import os
import subprocess
import sys
import threading
from pathlib import Path
from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.core.config import settings
from app.models.knowledge import Workspace, Chunk, Document

worker_slot = threading.BoundedSemaphore(1)


def data_root():
    path = Path(settings.LOCAL_DATA_DIR).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def model_id():
    return f"{settings.EMBEDDING_BACKEND}:{settings.EMBEDDING_MODEL}"


def owned_workspace(db: Session, workspace_id, user_id):
    workspace = db.query(Workspace).filter_by(id=workspace_id, user_id=user_id).first()
    if workspace is None:
        raise HTTPException(404, "Workspace not found")
    return workspace


def run_worker(payload, timeout):
    if not worker_slot.acquire(blocking=False):
        raise HTTPException(429, "Local indexer busy. Please try again shortly.")
    try:
        # Do not forward app secrets to parsing subprocesses.
        env = {
            key: value
            for key, value in os.environ.items()
            if key.upper()
            in {
                "SYSTEMROOT",
                "WINDIR",
                "PATH",
                "TEMP",
                "TMP",
                "USERPROFILE",
                "LOCALAPPDATA",
                "APPDATA",
            }
        }
        env.update(
            OMP_NUM_THREADS="2", TOKENIZERS_PARALLELISM="false", HF_HUB_OFFLINE="1"
        )
        payload.update(
            backend=settings.EMBEDDING_BACKEND,
            model=settings.EMBEDDING_MODEL,
            cache=str(data_root() / "models"),
        )
        result = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("index_worker.py"))],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            env=env,
        )
        try:
            output = json.loads(result.stdout)
        except ValueError:
            raise HTTPException(422, "Index worker failed within resource limits")
        if "error" in output:
            raise HTTPException(422, output["error"])
        return output
    except subprocess.TimeoutExpired:
        raise HTTPException(
            408,
            "Indexing exceeded time limit. Reduce document size or prepare the model cache.",
        )
    finally:
        worker_slot.release()


def retrieve(db, workspace_id, question):
    rows = (
        db.query(Chunk, Document)
        .join(Document, Chunk.document_id == Document.id)
        .filter(
            Document.workspace_id == workspace_id,
            Document.status == "ready",
            Document.embedding_model == model_id(),
        )
        .limit(settings.MAX_WORKSPACE_CHUNKS)
        .all()
    )
    if not rows:
        return []
    vector = run_worker({"texts": [question]}, settings.QUERY_TIMEOUT_SECONDS)[
        "vectors"
    ][0]
    scored = []
    for chunk, document in rows:
        if len(vector) != len(chunk.vector):
            continue
        dot = sum(a * b for a, b in zip(vector, chunk.vector))
        norm = (
            math.sqrt(sum(a * a for a in vector) * sum(b * b for b in chunk.vector))
            or 1
        )
        score = dot / norm
        if score >= settings.RETRIEVAL_MIN_SCORE:
            scored.append((score, chunk, document))
    scored.sort(key=lambda item: (-item[0], item[1].id))
    return [
        {
            "number": i + 1,
            "chunk_id": chunk.id,
            "document_id": document.id,
            "document_name": document.name,
            "page": chunk.page,
            "content": chunk.content,
        }
        for i, (_, chunk, document) in enumerate(scored[:5])
    ]
