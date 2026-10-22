"""Local, content-free metrics. No remote telemetry or request/response body capture."""

import contextvars
import functools
import re
import time
import uuid
from datetime import datetime, timezone
from contextlib import contextmanager

current = contextvars.ContextVar("runtime_metrics", default=None)
phase = contextvars.ContextVar("provider_phase", default=None)
request_started = contextvars.ContextVar("request_started", default=None)
STAGES = frozenset({
    "coordination_preparation", "routing", "material_acquisition", "generation",
    "persistence", "cleanup", "follower_preparation", "follower_wait", "follower_result",
})
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


def observation_id():
    """Telemetry must never prevent an HTTP attempt or Flight from running."""
    try:
        return str(uuid.uuid4())
    except Exception:
        return None


@contextmanager
def chat_stage(name):
    """Inclusive local intervals only. Observation errors never replace business errors."""
    item, origin, start = None, None, None
    try:
        item = current.get()
        origin = request_started.get()
        if item is not None and item["operation"] == "chat" and origin is not None and name in STAGES:
            start = time.perf_counter()
    except Exception:
        pass
    outcome = "success"
    try:
        yield
    except BaseException:
        outcome = "failure"
        raise
    finally:
        try:
            if start is not None:
                end = time.perf_counter()
                item.setdefault("stages", []).append({
                    "name": name,
                    "start_ms": round((start - origin) * 1000, 3),
                    "end_ms": round((end - origin) * 1000, 3),
                    "duration_ms": round((end - start) * 1000, 3),
                    "outcome": outcome,
                })
        except Exception:
            pass


def finish_stage_coverage(item):
    """Union clipped stage intervals; Provider durations are nested, never added."""
    try:
        if item["operation"] != "chat":
            return
        intervals = sorted(
            (max(0, s["start_ms"]), min(item["server_elapsed_ms"], s["end_ms"]))
            for s in item.get("stages", [])
        )
        covered, right = 0.0, 0.0
        for start, end in intervals:
            if end > max(start, right):
                covered += end - max(start, right)
            right = max(right, end)
        item["stages"] = item.get("stages", [])
        item["stage_covered_ms"] = round(covered, 3)
        item["unexplained_ms"] = round(max(0, item["server_elapsed_ms"] - covered), 3)
    except Exception:
        pass


def singleflight_fields(**fields):
    """Internal, scalar-only observation updates; never capture request content."""
    try:
        item = current.get()
        if item is None:
            return
        for key, value in fields.items():
            if key in {"request_id", "flight_id"}:
                if value is None or str(uuid.UUID(value)) == value:
                    item[key] = value
            elif key == "singleflight_role" and value in {"leader", "follower", "rejected"}:
                item[key] = value
            elif key == "execution_source" and value in {
                "fresh_generation", "persisted_replay", "shared_result",
            }:
                item[key] = value
            elif key == "failure_origin" and value in {
                "shared_execution", "follower_wait_timeout", "follower_local",
                "registration_rejected",
            }:
                item[key] = value
            elif key == "error_type" and value in ERRORS:
                item[key] = value
    except Exception:
        pass


def failure_category(error):
    """Only an allowlisted category crosses from Leader to Followers."""
    try:
        category = getattr(error, "generation_category", None)
        if category in ERRORS:
            return category
        item = current.get()
        if item and item.get("error_type") in ERRORS:
            return item["error_type"]
        status = getattr(error, "status_code", 500)
        return {
            401: "unauthorized", 403: "forbidden", 404: "not_found",
            409: "conflict", 429: "busy",
        }.get(status, "server_error" if status >= 500 else "invalid_request")
    except Exception:
        return "server_error"


@contextmanager
def follower_wait():
    # Guard only observation work, never swallow a business exception from yield.
    try:
        started = time.perf_counter() if current.get() is not None else None
    except Exception:
        started = None
    try:
        yield
    finally:
        try:
            if started is not None:
                current.get()["follower_wait_ms"] = round(
                    (time.perf_counter() - started) * 1000, 2
                )
        except Exception:
            pass


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
                if name in {"routing", "knowledge_selection", "generation", "citation_audit", "recap"}
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
    from app.services.telemetry_writer import submit
    try:
        submit(event)
    except Exception:
        pass  # Best-effort observation cannot change business behavior.


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
            "http_request_id": observation_id(),
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
        clock_token = request_started.set(started)
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
            finish_stage_coverage(item)
            try:
                write_event(item)
            finally:
                request_started.reset(clock_token)
                current.reset(token)
