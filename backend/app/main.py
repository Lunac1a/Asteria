from contextlib import asynccontextmanager, ExitStack
import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.api.router import api_router
from app.core.config import settings
from app.core.request_limits import RequestLimits
from app.services.runtime_metrics import RuntimeMetrics, record_error
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from app.db.session import engine, get_db
from app.models.workspace_delete_operation import PENDING, WorkspaceDeleteOperation
from app.services.startup_recovery import recover_startup, storage_lease
from app.services.knowledge.query_supervisor import QueryWorkerSupervisor
from app.services.knowledge.retrieval import worker_slot, data_root

logger = logging.getLogger(__name__)


def create_app():
    enabled = settings.STARTUP_RECOVERY_ENABLED

    @asynccontextmanager
    async def lifespan(app):
        app.state.recovery_ready = False
        with ExitStack() as lifetime:
            try:
                if enabled:
                    # Explicit operational attestation is still required for legacy
                    # processes and external writers which do not use this lease.
                    from app.services.workspace_delete_recovery import require_maintenance
                    require_maintenance()
                    root = lifetime.enter_context(storage_lease(Path(os.path.abspath(settings.LOCAL_DATA_DIR))))
                    recover_startup(engine, root)
                app.state.recovery_ready = True
            except Exception:
                logger.exception("Startup recovery failed; business traffic remains disabled")
            if app.state.recovery_ready and settings.PERSISTENT_QUERY_WORKER_ENABLED:
                # Complete initialization before serving. Failure aborts startup;
                # runtime worker failures do not change the recovery readiness gate.
                app.state.recovery_ready = False
                if settings.EMBEDDING_BACKEND != "fastembed":
                    raise RuntimeError("Persistent query worker requires EMBEDDING_BACKEND=fastembed")
                worker = QueryWorkerSupervisor(settings.EMBEDDING_MODEL, data_root() / "models",
                    worker_slot, settings.PERSISTENT_QUERY_STARTUP_TIMEOUT_SECONDS,
                    restart_limit=settings.PERSISTENT_QUERY_RESTART_LIMIT,
                    backoff_seconds=settings.PERSISTENT_QUERY_RESTART_BACKOFF_SECONDS,
                    window_seconds=settings.PERSISTENT_QUERY_RESTART_WINDOW_SECONDS,
                    failed_cycle_limit=settings.PERSISTENT_QUERY_FAILED_CYCLE_LIMIT)
                app.state.query_worker = worker
                lifetime.callback(worker.close)
                worker.start()
                app.state.recovery_ready = True
            try:
                yield
            finally:
                app.state.recovery_ready = False

    application = FastAPI(
        title=settings.APP_NAME,
        description="Personal Learning Copilot — Local MVP",
        version="0.2.0",
        lifespan=lifespan,
    )
    application.state.recovery_ready = False
    application.state.query_worker = None

    @application.middleware("http")
    async def startup_gate(request: Request, call_next):
        if (not application.state.recovery_ready
                and request.url.path not in {"/api/health", "/api/ready"}):
            return JSONResponse(status_code=503, content={"detail": "Service is not ready"})
        return await call_next(request)

    @application.get("/api/ready", tags=["health"])
    def readiness(db: Session = Depends(get_db)):
        ready = application.state.recovery_ready
        if ready:
            try:
                ready = db.scalar(select(WorkspaceDeleteOperation.id).where(
                    WorkspaceDeleteOperation.state.in_(PENDING)).limit(1)) is None
            except SQLAlchemyError:
                db.rollback()
                ready = False
        return JSONResponse(status_code=200 if ready else 503, content={
            "ready": ready, "maintenance": settings.WORKSPACE_DELETE_MAINTENANCE_MODE,
        })

    @application.exception_handler(RequestValidationError)
    async def safe_validation_error(request: Request, error: RequestValidationError):
        # FastAPI defaults echo invalid input, including passwords and API keys.
        details = [
            {"loc": item["loc"], "msg": item["msg"], "type": item["type"]}
            for item in error.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": details})

    application.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.FRONTEND_URL],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(api_router)

    @application.exception_handler(StarletteHTTPException)
    async def categorized_error(request: Request, error: StarletteHTTPException):
        category = getattr(error, "generation_category", None)
        if category:
            record_error(category)
        return JSONResponse(
            status_code=error.status_code,
            content={"detail": error.detail},
            headers=error.headers,
        )

    application.add_middleware(RequestLimits)
    application.add_middleware(RuntimeMetrics)
    return application


app = create_app()
