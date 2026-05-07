"""Toss Payments subscription billing + RevenueCat mobile IAP webhooks."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from src.storage.user_store import get_user_store
from .session import COOKIE_NAME, create_session, decode_session, get_current_user

router = APIRouter()

TOSS_CLIENT_KEY = os.environ.get("TOSS_CLIENT_KEY", "")
TOSS_SECRET_KEY = os.environ.get("TOSS_SECRET_KEY", "")
TOSS_WEBHOOK_SECRET = os.environ.get("TOSS_WEBHOOK_SECRET", "")
REVENUECAT_WEBHOOK_SECRET = os.environ.get("REVENUECAT_WEBHOOK_SECRET", "")
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8000")
ADSENSE_PUB_ID = os.environ.get("ADSENSE_PUB_ID", "")
ADSENSE_REWARDED_SLOT = os.environ.get("ADSENSE_REWARDED_SLOT", "")

TOSS_API_BASE = "https://api.tosspayments.com/v1"
SUBSCRIPTION_MONTHS = 1
PLAN_AMOUNT = 8000  # KRW

PAYMENTS_ENABLED = os.environ.get("PAYMENTS_ENABLED", "false").lower() == "true"


def _toss_auth_header() -> str:
    encoded = base64.b64encode(f"{TOSS_SECRET_KEY}:".encode()).decode()
    return f"Basic {encoded}"


def _subscription_end() -> str:
    """Return ISO datetime 1 month from now."""
    return (datetime.now(timezone.utc) + timedelta(days=31)).isoformat()


# ── Web subscription flow ─────────────────────────────────────────────────────

def _require_payments():
    if not PAYMENTS_ENABLED:
        raise HTTPException(status_code=503, detail="결제 기능은 현재 준비 중입니다.")


@router.post("/api/subscribe")
async def subscribe(request: Request):
    """Initiate Toss Payments billing key issuance for a subscription."""
    _require_payments()
    if not TOSS_CLIENT_KEY:
        raise HTTPException(status_code=503, detail="결제 기능이 설정되지 않았습니다.")
    try:
        user = get_current_user(request)
    except HTTPException:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")

    customer_key = f"user_{hashlib.md5(user['email'].encode()).hexdigest()[:16]}"
    # Return billing page URL for frontend to redirect to
    return JSONResponse({
        "clientKey": TOSS_CLIENT_KEY,
        "customerKey": customer_key,
        "amount": PLAN_AMOUNT,
        "orderId": f"sub_{uuid.uuid4().hex[:16]}",
        "orderName": "입시상담 프리미엄 월정액",
        "successUrl": f"{APP_BASE_URL}/api/payments/callback",
        "failUrl": f"{APP_BASE_URL}/?payment=fail",
    })


@router.get("/api/payments/callback")
async def payments_callback(
    request: Request,
    authKey: str | None = None,
    customerKey: str | None = None,
    error: str | None = None,
    message: str | None = None,
):
    """Handle Toss billing key callback after user approves billing."""
    if error:
        return RedirectResponse(url=f"/?payment=fail&error={error}")
    if not authKey or not customerKey:
        return RedirectResponse(url="/?payment=fail&error=missing_params")

    try:
        user = get_current_user(request)
    except HTTPException:
        return RedirectResponse(url="/?payment=fail&error=not_logged_in")

    # Exchange authKey for billingKey
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{TOSS_API_BASE}/billing/authorizations/{authKey}",
            headers={"Authorization": _toss_auth_header(), "Content-Type": "application/json"},
            json={"customerKey": customerKey},
        )
        if resp.status_code != 200:
            return RedirectResponse(url=f"/?payment=fail&error=billing_auth_failed")
        billing_data = resp.json()

    billing_key = billing_data.get("billingKey", "")
    store = get_user_store()
    store.set_billing_key(user["email"], billing_key, customerKey)

    # Charge immediately for first month
    order_id = f"sub_{uuid.uuid4().hex[:16]}"
    async with httpx.AsyncClient() as client:
        charge_resp = await client.post(
            f"{TOSS_API_BASE}/billing/{billing_key}",
            headers={"Authorization": _toss_auth_header(), "Content-Type": "application/json"},
            json={
                "customerKey": customerKey,
                "amount": PLAN_AMOUNT,
                "orderId": order_id,
                "orderName": "입시상담 프리미엄 월정액",
                "customerEmail": user["email"],
                "customerName": user.get("name", ""),
            },
        )

    if charge_resp.status_code == 200:
        store.set_tier(user["email"], "paid", _subscription_end())
        # Re-issue JWT with updated tier
        new_token = create_session(
            user_id=user["sub"],
            email=user["email"],
            name=user.get("name", ""),
            picture=user.get("picture", ""),
            tier="paid",
        )
        response = RedirectResponse(url="/?payment=success", status_code=302)
        response.set_cookie(
            key=COOKIE_NAME, value=new_token, httponly=True,
            secure=APP_BASE_URL.startswith("https://"), samesite="lax",
            max_age=7 * 24 * 3600,
        )
        return response
    else:
        return RedirectResponse(url="/?payment=fail&error=charge_failed")


@router.post("/api/payments/webhook")
async def payments_webhook(request: Request):
    """Toss Payments webhook — handles recurring billing events."""
    body_bytes = await request.body()

    if TOSS_WEBHOOK_SECRET:
        sig = request.headers.get("Toss-Signature", "")
        expected = hmac.new(
            TOSS_WEBHOOK_SECRET.encode(), body_bytes, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    event = json.loads(body_bytes)
    event_type = event.get("eventType", "")
    store = get_user_store()

    if event_type == "BILLING_PAYMENT_DONE":
        data = event.get("data", {})
        customer_key = data.get("customerKey", "")
        # Find user by customer_key pattern
        email = _email_from_customer_key(customer_key)
        if email:
            store.set_tier(email, "paid", _subscription_end())

    elif event_type in ("BILLING_PAYMENT_FAILED", "BILLING_CANCELED"):
        data = event.get("data", {})
        customer_key = data.get("customerKey", "")
        email = _email_from_customer_key(customer_key)
        if email:
            # Only downgrade if subscription actually expired
            current_tier = store.get_tier(email)
            if current_tier == "paid":
                store.set_tier(email, "free", None)

    return JSONResponse({"ok": True})


def _email_from_customer_key(customer_key: str) -> str | None:
    """Reverse-look up email from customer_key by scanning users DB."""
    import sqlite3
    from pathlib import Path
    db_path = Path(__file__).parent.parent.parent / "data" / "users.db"
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT email FROM users WHERE customer_key = ?", (customer_key,)
    ).fetchone()
    conn.close()
    return row["email"] if row else None


# ── RevenueCat mobile IAP webhook ─────────────────────────────────────────────

@router.post("/api/payments/revenuecat")
async def revenuecat_webhook(request: Request):
    """RevenueCat webhook — handles mobile in-app purchase events."""
    body_bytes = await request.body()

    if REVENUECAT_WEBHOOK_SECRET:
        auth = request.headers.get("Authorization", "")
        if auth != REVENUECAT_WEBHOOK_SECRET:
            raise HTTPException(status_code=401, detail="Invalid RevenueCat secret")

    event = json.loads(body_bytes)
    event_type = event.get("event", {}).get("type", "")
    app_user_id = event.get("event", {}).get("app_user_id", "")
    store = get_user_store()

    # app_user_id is set to user email in mobile app
    if event_type in ("INITIAL_PURCHASE", "RENEWAL", "PRODUCT_CHANGE", "UNCANCELLATION"):
        store.set_tier(app_user_id, "paid", _subscription_end())
    elif event_type in ("CANCELLATION", "EXPIRATION", "BILLING_ISSUE"):
        if store.get_tier(app_user_id) == "paid":
            store.set_tier(app_user_id, "free", None)

    return JSONResponse({"ok": True})


# ── Public config ────────────────────────────────────────────────────────────

_IS_DEV = APP_BASE_URL.startswith("http://localhost") or APP_BASE_URL.startswith("http://127.0.0.1")


@router.get("/api/config")
async def public_config():
    """Return public frontend configuration (AdSense IDs, feature flags)."""
    from src.storage.user_store import get_user_store
    store = get_user_store()
    daily_free_limit = int(store.get_config("daily_free_limit", "5"))
    return JSONResponse({
        "adsense_pub_id": ADSENSE_PUB_ID,
        "adsense_rewarded_slot": ADSENSE_REWARDED_SLOT,
        "ad_credits_max": 4,
        "toss_enabled": bool(TOSS_CLIENT_KEY) and PAYMENTS_ENABLED,
        "payments_enabled": PAYMENTS_ENABLED,
        "daily_free_limit": daily_free_limit,
        "dev_mode": _IS_DEV,
    })


# ── Ad credit endpoint ────────────────────────────────────────────────────────

@router.post("/api/credits/ad")
async def ad_credit(request: Request):
    """Grant 1 extra question after user watches an ad (mobile free tier)."""
    user = get_current_user(request)
    user_id = user.get("sub", user.get("id", ""))
    tier = user.get("tier", "free")

    if tier == "paid":
        return JSONResponse({"ad_credits": 0, "message": "프리미엄 사용자는 광고 크레딧이 필요하지 않습니다."})

    from .rate_limit import grant_ad_credit, _AD_CREDIT_MAX_PER_DAY
    credits = grant_ad_credit(user_id)
    return JSONResponse({
        "ad_credits": credits,
        "ad_credits_max": _AD_CREDIT_MAX_PER_DAY,
        "can_watch_more": credits < _AD_CREDIT_MAX_PER_DAY,
    })
