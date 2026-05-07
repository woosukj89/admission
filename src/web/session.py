"""JWT session utilities for the web client."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from jose import JWTError, jwt

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-in-production")
ALGORITHM = "HS256"
SESSION_DAYS = 7
COOKIE_NAME = "session"


def create_session(
    user_id: str,
    email: str,
    name: str,
    picture: str,
    tier: str = "free",
) -> str:
    """Create a signed JWT session token."""
    expire = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    payload = {
        "sub": user_id,
        "email": email,
        "name": name,
        "picture": picture,
        "tier": tier,
        "exp": expire,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=ALGORITHM)


def decode_session(token: str) -> Optional[dict]:
    """Decode and validate a JWT session token. Returns None if invalid/expired."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
    except JWTError:
        return None


def get_current_user(request: Request) -> dict:
    """Extract user from session cookie or Authorization header.

    Supports both web (cookie) and mobile (Bearer token) clients.
    Raises 401 if missing/invalid.
    """
    # Try cookie first (web), then Authorization header (mobile)
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        _unauthorized("로그인이 필요합니다.")
    payload = decode_session(token)
    if not payload:
        _unauthorized("세션이 만료되었습니다. 다시 로그인해 주세요.")
    return payload


def get_optional_user(request: Request) -> dict | None:
    """Like get_current_user but returns None instead of raising 401."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        return None
    return decode_session(token)  # None if invalid/expired


def _unauthorized(message: str):
    from fastapi import HTTPException
    raise HTTPException(status_code=401, detail=message)
