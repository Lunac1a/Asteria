"""Local, content-free metrics. No remote telemetry or request/response body capture."""

import contextvars
import functools
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from contextlib import contextmanager

current = contextvars.ContextVar("runtime_metrics", default=None)
phase = contextvars.ContextVar("provider_phase", default=None)
_lock = threading.Lock()
ERRORS = {
    "provider_timeout",
    "provider_error",
    "provider_protocol",
    "provider_connection",
    "provider_status",
    "provider_rate_limit",
    "provider_refusal",
    "output_truncated",
    "incomplete_response",
    "empty_response",
    "json_parse",
    "schema_validation",
    "citation_validation",
    "routing_format",
    "retrieval_failure",
    "unauthorized",
    "forbidden",
    "not_found",
    "conflict",
    "busy",
    "invalid_request",
    "server_error",
    "network",
    "aborted",
}


def record_error(category):
    item = current.get()
    if item is not None:
        item["error_type"] = category if category in ERRORS else "server_error"


@contextmanager
def provider_phase(value):
    token = phase.set(value)
    try:
        yield
    finally:
        phase.reset(token)


def provider_call(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        item = current.get()
        if item is None:
            return function(*args, **kwargs)
        started = time.perf_counter()
        metadata = {}
        error = None
        try:
            result = function(*args, **kwargs)
            metadata = getattr(result, "metadata", {})
            return result
        except Exception as exc:
            metadata = getattr(exc, "metadata", {})
            error = getattr(exc, "category", "provider_error")
            record_error(error)
            raise
        finally:
            name = phase.get() or (
                "recap" if item["operation"] == "recap" else "generation"
            )
            call = {
                "phase": name
                if name in {"routing", "generation", "citation_audit", "recap"}
                else "generation",
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                "error_type": error
                if error in ERRORS
                else ("provider_error" if error else None),
            }
            for key in ["prompt_tokens", "completion_tokens", "total_tokens"]:
                value = metadata.get(key) if isinstance(metadata, dict) else None
                call[key] = value if type(value) is int and value >= 0 else None
            item["provider_calls"].append(call)

    return wrapped


def write_event(event):
    from app.core.config import settings

    if not settings.RUNTIME_METRICS_ENABLED:
        return
    try:
        path = (Path(settings.LOCAL_DATA_DIR) / "runtime" / "metrics.jsonl").resolve()
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            try:
                record = logging.LogRecord(
                    "runtime_metrics",
                    logging.INFO,
                    "",
                    0,
                    json.dumps(event, separators=(",", ":")),
                    (),
                    None,
                )
                handler.emit(record)
            finally:
                handler.close()
    except Exception:
        # Observability failure must not fail or replay a learning action; no exception data.
        logging.getLogger(__name__).warning("Local runtime metrics unavailable")


def operation(path):
    if path == "/api/chat":
        return "chat"
    if "/learning/recap" in path:
        return "recap"
    if "/learning" in path:
        return "learning_state"
    if path.startswith("/api/workspaces"):
        return "workspace"
    if path.startswith("/api/chat/"):
        return "session"
    if path.startswith("/api/dashboard"):
        return "dashboard"
    if path in {"/api/login", "/api/register"}:
        return "auth"
    if path in {"/api/health", "/api/db-health"}:
        return "health"
    return "other_api"


class RuntimeMetrics:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        from app.core.config import settings

        if (
            scope["type"] != "http"
            or not settings.RUNTIME_METRICS_ENABLED
            or not scope["path"].startswith("/api/")
            or scope["path"] == "/api/runtime-metrics/client"
        ):
            return await self.app(scope, receive, send)
        raw = (
            dict(scope.get("headers", []))
            .get(b"x-asteria-trace", b"")
            .decode("ascii", errors="ignore")
        )
        trace = (
            raw
            if re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", raw)
            else str(uuid.uuid4())
        )
        item = {
            "version": "runtime-v1",
            "kind": "server_request",
            "trace_id": trace,
            "operation": operation(scope["path"]),
            "llm_backend": settings.LLM_BACKEND,
            "provider_simulated": settings.PROVIDER_IS_SIMULATED,
            "method": scope["method"]
            if scope["method"] in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
            else "OTHER",
            "provider_calls": [],
            "error_type": None,
        }
        token = current.set(item)
        started = time.perf_counter()
        status = 500
        completed = False

        async def observed_send(message):
            nonlocal status, completed
            if message["type"] == "http.response.start":
                status = message["status"]
                message = {
                    **message,
                    "headers": [
                        *message.get("headers", []),
                        (b"x-asteria-trace", trace.encode()),
                    ],
                }
            await send(message)
            if message["type"] == "http.response.body" and not message.get(
                "more_body", False
            ):
                completed = True

        try:
            await self.app(scope, receive, observed_send)
        except BaseException:
            if not item["error_type"]:
                record_error("server_error")
            raise
        finally:
            item.update(
                status=status,
                response_completed=completed,
                server_elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
                timestamp_utc=datetime.now(timezone.utc).isoformat(),
            )
            if status >= 400 and not item["error_type"]:
                item["error_type"] = {
                    401: "unauthorized",
                    403: "forbidden",
                    404: "not_found",
                    409: "conflict",
                    429: "busy",
                }.get(status, "server_error" if status >= 500 else "invalid_request")
            calls = item["provider_calls"]
            item["provider_call_attempts"] = len(calls)
            item["usage_complete"] = all(c["total_tokens"] is not None for c in calls)
            item["known_total_tokens"] = sum(c["total_tokens"] or 0 for c in calls)
            item["total_tokens"] = (
                item["known_total_tokens"] if item["usage_complete"] else None
            )
            write_event(item)
            current.reset(token)
