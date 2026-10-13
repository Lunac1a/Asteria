from fastapi import APIRouter
from app.api.routes import runtime_metrics
from app.api.routes import document_recovery
from app.api.routes import health, auth, chat, llm_settings, knowledge, dashboard, learning

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, tags=["auth"])
api_router.include_router(chat.router, tags=["chat"])
api_router.include_router(llm_settings.router, tags=["llm-settings"])
api_router.include_router(knowledge.router, tags=["knowledge"])
api_router.include_router(dashboard.router, tags=["dashboard"])

api_router.include_router(learning.router, tags=["learning"])
api_router.include_router(runtime_metrics.router, tags=["local-metrics"])
api_router.include_router(document_recovery.router, tags=["admin-recovery"])
