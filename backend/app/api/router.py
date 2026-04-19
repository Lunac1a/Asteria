from fastapi import APIRouter
from app.api.routes import health, auth, debug

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(debug.router, prefix="/debug", tags=["debug"])