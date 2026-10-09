from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.api.router import api_router
from app.core.config import settings
from app.core.request_limits import RequestLimits
from app.services.runtime_metrics import RuntimeMetrics, record_error
from starlette.exceptions import HTTPException as StarletteHTTPException


def create_app():
    application = FastAPI(
        title=settings.APP_NAME,
        description="Personal Learning Copilot — Local MVP",
        version="0.2.0",
    )

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
        category = getattr(error, 'generation_category', None)
        if category: record_error(category)
        return JSONResponse(status_code=error.status_code, content={'detail': error.detail}, headers=error.headers)
    application.add_middleware(RequestLimits)
    application.add_middleware(RuntimeMetrics)
    return application


app = create_app()
