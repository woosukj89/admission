"""FastAPI router that aggregates all web client routes."""
from __future__ import annotations

from fastapi import APIRouter

from .auth import router as auth_router
from .chat import router as chat_router
from .payments import router as payments_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(chat_router)
router.include_router(payments_router)
