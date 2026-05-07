"""Vercel Python serverless entry point — wraps the FastAPI app."""
import sys
from pathlib import Path

# Ensure project root is on sys.path so `src` is importable
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.api import app  # noqa: E402 — the FastAPI application

# Vercel's Python runtime expects an ASGI `app` in this module.
# The import above is sufficient; no additional wiring needed.
