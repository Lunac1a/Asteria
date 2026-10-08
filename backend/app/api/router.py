from fastapi import APIRouter
from app.api.routes import health, auth, chat, llm_settings, knowledge

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, tags=["auth"])
api_router.include_router(chat.router, tags=["chat"])
api_router.include_router(llm_settings.router, tags=["llm-settings"])
api_router.include_router(knowledge.router, tags=["knowledge"])
