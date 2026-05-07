"""Rate limiter: per-user daily + monthly limits by tier, with ad credits."""
from __future__ import annotations

import time
from datetime import date, datetime, timezone
from threading import Lock

_LIMIT_CACHE: dict[str, tuple[int, float]] = {}  # tier → (limit, cached_at)
_LIMIT_CACHE_TTL = 60.0  # seconds


def _get_daily_limit(tier: str) -> int:
    """Read daily limit from DB config with 60s TTL cache."""
    now = time.monotonic()
    cached = _LIMIT_CACHE.get(tier)
    if cached and now - cached[1] < _LIMIT_CACHE_TTL:
        return cached[0]
    try:
        from src.storage.user_store import get_user_store
        store = get_user_store()
        if tier == "free":
            val = int(store.get_config("daily_free_limit", "5"))
        elif tier == "paid":
            val = int(store.get_config("daily_paid_limit", "5"))
        elif tier == "premium":
            val = 9999
        else:
            val = int(store.get_config("daily_free_limit", "5"))
    except Exception:
        val = 5
    _LIMIT_CACHE[tier] = (val, now)
    return val


LIMITS: dict[str, dict] = {
    "free":    {"monthly": None},
    "paid":    {"monthly": None},
    "premium": {"monthly": None},
}

_daily: dict[str, tuple[int, date]] = {}   # user_id → (count, date)
_monthly: dict[str, int] = {}              # user_id → count this calendar month
_ad_credits: dict[str, int] = {}          # user_id → extra credits from ads (resets daily)
_ad_credit_dates: dict[str, date] = {}    # user_id → date of last ad credit grant
_month_key: str = ""                      # "YYYY-MM" of current month
_lock = Lock()

_AD_CREDIT_MAX_PER_DAY = 4  # free=1/day + 4 ads = 5 total


def _current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _reset_month_if_needed() -> None:
    global _month_key, _monthly
    m = _current_month()
    if m != _month_key:
        _monthly = {}
        _month_key = m


def check_and_increment(user_id: str, tier: str = "free") -> bool:
    """Returns True if within limits and increments count; False if limit exceeded.

    Checks daily limit first, then monthly (if applicable).
    Free users can spend ad_credits after hitting their daily limit.
    """
    cfg = LIMITS.get(tier, LIMITS["free"])
    daily_limit: int = _get_daily_limit(tier)
    monthly_limit: int | None = cfg["monthly"]
    today = date.today()

    with _lock:
        _reset_month_if_needed()

        # Daily check
        count, day = _daily.get(user_id, (0, today))
        if day != today:
            count = 0
        monthly_count = _monthly.get(user_id, 0)

        # Try ad credits for free users when daily limit hit
        if count >= daily_limit:
            # Reset ad credits if a new day
            credit_date = _ad_credit_dates.get(user_id, today)
            credits = _ad_credits.get(user_id, 0)
            if credit_date != today:
                credits = 0
                _ad_credits[user_id] = 0
                _ad_credit_dates[user_id] = today
            if credits > 0:
                _ad_credits[user_id] = credits - 1
                _monthly[user_id] = monthly_count + 1
                return True
            return False

        # Monthly check
        if monthly_limit is not None and monthly_count >= monthly_limit:
            return False

        _daily[user_id] = (count + 1, today)
        _monthly[user_id] = monthly_count + 1
        return True


def grant_ad_credit(user_id: str) -> int:
    """Grant 1 extra question via ad watch. Returns new ad_credits count."""
    today = date.today()
    with _lock:
        credit_date = _ad_credit_dates.get(user_id, today)
        credits = _ad_credits.get(user_id, 0)
        if credit_date != today:
            credits = 0
        if credits >= _AD_CREDIT_MAX_PER_DAY:
            return credits
        credits += 1
        _ad_credits[user_id] = credits
        _ad_credit_dates[user_id] = today
        return credits


def get_usage(user_id: str, tier: str = "free") -> dict:
    """Return current usage info for a user."""
    cfg = LIMITS.get(tier, LIMITS["free"])
    daily_limit: int = _get_daily_limit(tier)
    monthly_limit: int | None = cfg["monthly"]
    today = date.today()

    with _lock:
        _reset_month_if_needed()
        count, day = _daily.get(user_id, (0, today))
        if day != today:
            count = 0
        monthly_count = _monthly.get(user_id, 0)
        credit_date = _ad_credit_dates.get(user_id, today)
        credits = _ad_credits.get(user_id, 0)
        if credit_date != today:
            credits = 0

    return {
        "daily_used": count,
        "daily_limit": daily_limit,
        "monthly_used": monthly_count,
        "monthly_limit": monthly_limit,
        "ad_credits": credits,
        # Legacy field kept for backwards compat with frontend
        "used": count,
        "limit": daily_limit,
        "remaining": max(0, daily_limit - count + credits),
    }
