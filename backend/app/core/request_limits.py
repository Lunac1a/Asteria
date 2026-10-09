import asyncio
import time
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from app.core.config import settings


class RequestLimits:
    """Bound request bodies before multipart spooling and reject slow uploads."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        multipart = b"multipart/form-data" in headers.get(b"content-type", b"")
        cap = settings.MAX_UPLOAD_BYTES + 65536 if multipart else 65536
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse(
                {"detail": "Invalid content length"}, status_code=400
            )(scope, receive, send)
        if length > cap:
            return await JSONResponse(
                {"detail": "Request exceeds local size limit"}, status_code=413
            )(scope, receive, send)
        received = 0
        deadline = time.monotonic() + 60

        async def limited_receive():
            nonlocal received
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HTTPException(408, "Upload time limit exceeded")
            try:
                event = await asyncio.wait_for(receive(), timeout=min(15, remaining))
            except TimeoutError:
                raise HTTPException(408, "Upload time limit exceeded")
            received += len(event.get("body", b""))
            if received > cap:
                raise HTTPException(413, "Request exceeds local size limit")
            return event

        await self.app(scope, limited_receive, send)
