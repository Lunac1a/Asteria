"""Disposable parser/embedder process. No database or provider credentials needed."""

import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path


def limit_memory():
    # Fail closed if OS resource limits cannot be installed.
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [
                ("per_process", ctypes.c_int64),
                ("per_job", ctypes.c_int64),
                ("flags", wintypes.DWORD),
                ("min_ws", ctypes.c_size_t),
                ("max_ws", ctypes.c_size_t),
                ("active", wintypes.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD),
            ]

        class IO(ctypes.Structure):
            _fields_ = [(str(i), ctypes.c_uint64) for i in range(6)]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("basic", Basic),
                ("io", IO),
                ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t),
                ("peak_job", ctypes.c_size_t),
            ]

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateJobObjectW.restype = wintypes.HANDLE
        api.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        api.GetCurrentProcess.restype = wintypes.HANDLE
        job = api.CreateJobObjectW(None, None)
        info = Extended()
        info.basic.flags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
        info.process_memory = 1536 * 1024 * 1024
        if (
            not job
            or not api.SetInformationJobObject(
                job, 9, ctypes.byref(info), ctypes.sizeof(info)
            )
            or not api.AssignProcessToJobObject(job, api.GetCurrentProcess())
        ):
            raise RuntimeError("Unable to install parser memory limit")
    else:
        import resource

        cap = 1536 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))


def parse(path):
    path = Path(path)
    if path.suffix == ".pdf":
        from pypdf import PdfReader

        if not path.read_bytes()[:1024].lstrip().startswith(b"%PDF-"):
            raise ValueError("Invalid PDF signature")
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError("Encrypted PDFs are not supported")
        if len(reader.pages) > 100:
            raise ValueError("Maximum 100 PDF pages")
        pages = [
            (i + 1, page.extract_text() or "") for i, page in enumerate(reader.pages)
        ]
    else:
        pages = [(None, path.read_text(encoding="utf-8"))]
    if sum(len(text) for _, text in pages) > 300_000:
        raise ValueError("Maximum 300,000 extracted characters")
    chunks = []
    for page, text in pages:
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        for start in range(0, len(text), 800):
            chunks.append({"page": page, "content": text[start : start + 1000]})
    if not chunks:
        raise ValueError(
            "No readable text. Scanned PDFs need OCR and are not supported."
        )
    if len(chunks) > 400:
        raise ValueError("Maximum 400 chunks per document")
    return chunks


def embed(texts, backend, model, cache, query=False):
    if backend == "test":
        # Explicit engineering test double, never a semantic model.
        vectors = []
        for text in texts:
            vector = [0.0] * 128
            for token in re.findall(r"\w+", text.lower()):
                vector[
                    int.from_bytes(hashlib.sha256(token.encode()).digest()[:2], "big")
                    % 128
                ] += 1
            norm = math.sqrt(sum(x * x for x in vector)) or 1
            vectors.append([x / norm for x in vector])
        return vectors
    from fastembed import TextEmbedding

    encoder = TextEmbedding(
        model_name=model, cache_dir=cache, threads=2, local_files_only=True
    )
    method = encoder.query_embed if query else encoder.passage_embed
    return [vector.tolist() for vector in method(texts, batch_size=16)]


def main():
    limit_memory()
    payload = json.loads(sys.stdin.read())
    chunks = parse(payload["path"]) if "path" in payload else []
    texts = [chunk["content"] for chunk in chunks] if chunks else payload["texts"]
    vectors = embed(
        texts, payload["backend"], payload["model"], payload["cache"], query=not chunks
    )
    if chunks:
        for chunk, vector in zip(chunks, vectors, strict=True):
            chunk["vector"] = vector
        return {"chunks": chunks}
    return {"vectors": vectors}


if __name__ == "__main__":
    try:
        print(json.dumps(main()))
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}))
    except Exception:
        # Parser/model exceptions may contain source text or local credentials.
        print(
            json.dumps(
                {
                    "error": "Index worker failed. Check model cache, file format and resource limits."
                }
            )
        )
