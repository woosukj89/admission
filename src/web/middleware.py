"""Request logging middleware: records every API call to analytics_store."""
from __future__ import annotations

import time

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from src.storage.analytics_store import get_analytics_store

_SKIP_PREFIXES = ("/static", "/_", "/favicon")


class RequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = int((time.monotonic() - start) * 1000)

        path = request.url.path
        if any(path.startswith(p) for p in _SKIP_PREFIXES):
            return response

        # Extract identity (best-effort, no hard dependency on session)
        user_email: str | None = None
        anon_id: str | None = None
        try:
            from src.web.session import get_optional_user
            user = get_optional_user(request)
            if user:
                user_email = user.get("email")
            else:
                anon_id = request.cookies.get("anon_session")
        except Exception:
            pass

        try:
            get_analytics_store().log_api_call(
                endpoint=path,
                method=request.method,
                user_email=user_email,
                anon_id=anon_id,
                status_code=response.status_code,
                duration_ms=duration_ms,
            )
        except Exception:
            pass  # never let analytics break the request

        return response
