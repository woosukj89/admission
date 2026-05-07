"""Google OAuth2 + email/password authentication endpoints."""
from __future__ import annotations

import os
import random
import smtplib
import string
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from .session import COOKIE_NAME, create_session
from src.storage.user_store import get_user_store

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER)


def _send_verification_email(to_email: str, code: str) -> None:
    if not SMTP_HOST or not SMTP_USER:
        return  # silently skip in dev when SMTP not configured
    msg = EmailMessage()
    msg["Subject"] = f"[입시 AI] 이메일 인증 코드: {code}"
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg.set_content(
        f"안녕하세요!\n\n입시 AI 상담사 회원가입을 위한 인증 코드입니다.\n\n"
        f"인증 코드: {code}\n\n"
        f"코드는 10분간 유효합니다.\n\n감사합니다."
    )
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls()
        s.login(SMTP_USER, SMTP_PASSWORD)
        s.send_message(msg)


def _gen_code() -> str:
    return "".join(random.choices(string.digits, k=6))

router = APIRouter()

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8000")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
SCOPES = "openid email profile"


@router.get("/auth/google")
async def auth_google():
    """Redirect to Google OAuth2 authorization page."""
    if not GOOGLE_CLIENT_ID:
        return JSONResponse({"error": "GOOGLE_CLIENT_ID not configured"}, status_code=503)

    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": f"{APP_BASE_URL}/auth/callback",
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "online",
        "prompt": "select_account",
    }
    return RedirectResponse(url=f"{GOOGLE_AUTH_URL}?{urlencode(params)}")


@router.get("/auth/callback")
async def auth_callback(code: str | None = None, error: str | None = None):
    """Handle Google OAuth2 callback."""
    if error:
        return RedirectResponse(url="/?error=" + error)
    if not code:
        return RedirectResponse(url="/?error=no_code")

    # Exchange code for tokens
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": f"{APP_BASE_URL}/auth/callback",
                "grant_type": "authorization_code",
            },
        )
        if token_resp.status_code != 200:
            return RedirectResponse(url="/?error=token_exchange_failed")

        token_data = token_resp.json()
        access_token = token_data.get("access_token")

        # Get user info
        userinfo_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if userinfo_resp.status_code != 200:
            return RedirectResponse(url="/?error=userinfo_failed")

        user = userinfo_resp.json()

    email = user.get("email", "")
    google_sub = user.get("sub", "")

    # Upsert user in DB and read their current tier
    store = get_user_store()
    store.upsert_user(
        email=email,
        google_sub=google_sub,
        name=user.get("name", ""),
        picture=user.get("picture", ""),
    )
    tier = store.get_tier(email)

    # Create session JWT
    session_token = create_session(
        user_id=google_sub,
        email=email,
        name=user.get("name", ""),
        picture=user.get("picture", ""),
        tier=tier,
    )

    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        key=COOKIE_NAME,
        value=session_token,
        httponly=True,
        secure=APP_BASE_URL.startswith("https://"),
        samesite="lax",
        max_age=7 * 24 * 3600,
    )
    return response


@router.get("/auth/logout")
async def auth_logout():
    """Clear session cookie and redirect to home."""
    response = RedirectResponse(url="/", status_code=302)
    response.delete_cookie(key=COOKIE_NAME)
    return response


@router.get("/api/profile")
async def get_profile(request: Request):
    """Return saved student profile for the logged-in user."""
    from .session import get_optional_user
    user = get_optional_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    profile = get_user_store().get_profile(user.get("email", ""))
    return JSONResponse(profile or {})


class ProfileUpdate(BaseModel):
    gender: str | None = None
    school_name: str | None = None
    school_region: str | None = None
    school_type: str | None = None
    graduation_year: int | None = None
    track: str | None = None
    interests: list[str] | None = None


@router.put("/api/profile")
async def update_profile(request: Request, body: ProfileUpdate):
    """Save/update student profile for the logged-in user."""
    from .session import get_optional_user
    import json as _json
    user = get_optional_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    email = user.get("email", "")
    fields = body.model_dump(exclude_none=True)
    if "interests" in fields:
        fields["interests"] = _json.dumps(fields["interests"], ensure_ascii=False)
    get_user_store().upsert_profile(email, **fields)
    return JSONResponse({"message": "프로필이 저장되었습니다."})


@router.get("/api/me")
async def api_me(request: Request):
    """Return current user info from session cookie."""
    from .session import decode_session, get_current_user
    try:
        payload = get_current_user(request)
    except Exception:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    # Always read fresh tier from DB (JWT may be stale after payment)
    store = get_user_store()
    tier = store.get_tier(payload.get("email", ""))
    return JSONResponse({
        "id": payload.get("sub"),
        "email": payload.get("email"),
        "name": payload.get("name"),
        "picture": payload.get("picture"),
        "tier": tier,
    })


class MobileAuthRequest(BaseModel):
    id_token: str


@router.post("/api/auth/mobile")
async def mobile_auth(body: MobileAuthRequest):
    """Verify Google ID token from mobile app; return JWT."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"id_token": body.id_token},
        )
        if resp.status_code != 200:
            return JSONResponse({"error": "Invalid Google ID token"}, status_code=401)
        info = resp.json()

    email = info.get("email", "")
    google_sub = info.get("sub", "")
    if not email or not google_sub:
        return JSONResponse({"error": "Token missing email/sub"}, status_code=401)

    store = get_user_store()
    store.upsert_user(
        email=email,
        google_sub=google_sub,
        name=info.get("name", ""),
        picture=info.get("picture", ""),
    )
    tier = store.get_tier(email)

    token = create_session(
        user_id=google_sub,
        email=email,
        name=info.get("name", ""),
        picture=info.get("picture", ""),
        tier=tier,
    )
    return JSONResponse({"token": token, "tier": tier})


# ── Email + password auth ─────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: str
    password: str


class VerifyRequest(BaseModel):
    email: str
    code: str


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/api/auth/register")
async def register(body: RegisterRequest):
    """Start email+password registration. Sends a 6-digit verification code."""
    import bcrypt
    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="올바른 이메일 주소를 입력해 주세요.")
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="비밀번호는 6자 이상이어야 합니다.")

    store = get_user_store()
    existing = store.get_email_user(email)
    if existing and existing["email_verified"]:
        raise HTTPException(status_code=409, detail="이미 가입된 이메일입니다. 로그인해 주세요.")

    pw_hash = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
    code = _gen_code()
    expires = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()

    if existing:
        store.set_verification_code(email, code, expires)
        # Update password hash for re-registration attempt
        with store._conn() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE email = ?", (pw_hash, email)
            )
    else:
        store.create_email_user(email, pw_hash, code, expires)

    try:
        _send_verification_email(email, code)
    except Exception:
        pass  # don't fail registration if email sending fails

    return JSONResponse({"message": "인증 코드를 이메일로 발송했습니다.", "email": email})


@router.post("/api/auth/verify-email")
async def verify_email(body: VerifyRequest):
    """Verify 6-digit code and activate account. Returns JWT session."""
    email = body.email.strip().lower()
    store = get_user_store()
    row = store.get_email_user(email)
    if not row:
        raise HTTPException(status_code=404, detail="가입 정보를 찾을 수 없습니다.")
    if row["email_verified"]:
        raise HTTPException(status_code=409, detail="이미 인증된 이메일입니다.")

    stored_code = row["verification_code"] or ""
    expires_str = row["verification_expires"] or ""
    if body.code.strip() != stored_code:
        raise HTTPException(status_code=400, detail="인증 코드가 올바르지 않습니다.")

    if expires_str:
        exp = datetime.fromisoformat(expires_str)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > exp:
            raise HTTPException(status_code=400, detail="인증 코드가 만료되었습니다. 다시 발송해 주세요.")

    store.verify_email(email)
    token = create_session(
        user_id=email,
        email=email,
        name=row["name"] or email.split("@")[0],
        picture="",
        tier="free",
    )
    response = JSONResponse({"message": "이메일 인증이 완료되었습니다.", "tier": "free"})
    response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax", max_age=7 * 24 * 3600)
    return response


@router.post("/api/auth/resend-verification")
async def resend_verification(body: VerifyRequest):
    """Resend verification code (only email field used from body)."""
    email = body.email.strip().lower()
    store = get_user_store()
    row = store.get_email_user(email)
    if not row:
        raise HTTPException(status_code=404, detail="가입 정보를 찾을 수 없습니다.")
    if row["email_verified"]:
        return JSONResponse({"message": "이미 인증된 이메일입니다."})

    code = _gen_code()
    expires = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    store.set_verification_code(email, code, expires)
    try:
        _send_verification_email(email, code)
    except Exception:
        pass
    return JSONResponse({"message": "인증 코드를 다시 발송했습니다."})


@router.post("/api/auth/login")
async def email_login(body: LoginRequest):
    """Login with email + password. Returns JWT session cookie."""
    import bcrypt
    email = body.email.strip().lower()
    store = get_user_store()
    row = store.get_email_user(email)
    if not row or not row["password_hash"]:
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다.")
    if not row["email_verified"]:
        raise HTTPException(status_code=403, detail="이메일 인증이 필요합니다. 인증 코드를 확인해 주세요.")
    if not bcrypt.checkpw(body.password.encode(), row["password_hash"].encode()):
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다.")

    tier = store.get_tier(email)
    token = create_session(
        user_id=email,
        email=email,
        name=row["name"] or email.split("@")[0],
        picture=row["picture"] or "",
        tier=tier,
    )
    response = JSONResponse({"message": "로그인되었습니다.", "tier": tier})
    response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax", max_age=7 * 24 * 3600)
    return response
