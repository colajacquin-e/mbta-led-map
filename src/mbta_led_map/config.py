"""Configuration for the MBTA LED Map server, read from environment variables."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _get_int(key: str, default: int) -> int:
    raw = os.getenv(key, "")
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default


# MBTA API key — optional; without it the API still works but at lower rate limits.
MBTA_API_KEY: str = os.getenv("MBTA_API_KEY", "")

# How often to poll the MBTA Vehicles API (seconds).
POLL_INTERVAL_SECONDS: int = _get_int("POLL_INTERVAL_SECONDS", 10)

# FastAPI / uvicorn bind address and port.
HOST: str = os.getenv("HOST", "0.0.0.0")
PORT: int = _get_int("PORT", 8000)
