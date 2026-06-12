"""Shared Vercel Edge Config reader with 60s in-memory cache."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any

_cache: dict[str, tuple[object, float]] = {}
_TTL = 60.0


async def get_edge_config(key: str) -> Any | None:
    """Read a key from Vercel Edge Config with 60s in-memory cache.

    Returns None when EDGE_CONFIG is unset (local dev).
    """
    import urllib.request

    now = time.monotonic()
    if key in _cache:
        val, ts = _cache[key]
        if now - ts < _TTL:
            return val

    edge_config = os.environ.get("EDGE_CONFIG", "").strip()
    if not edge_config:
        return None

    m = re.match(r"(https://edge-config\.vercel\.com/[^?]+)\?token=(.+)", edge_config)
    if not m:
        return None
    base_url, token = m.group(1), m.group(2)

    try:
        def _fetch() -> object:
            req = urllib.request.Request(
                f"{base_url}/item/{key}",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read())
        val = await asyncio.to_thread(_fetch)
    except Exception:
        val = None

    _cache[key] = (val, now)
    return val
