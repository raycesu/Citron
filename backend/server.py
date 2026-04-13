"""
Legacy shim — kept for local uvicorn convenience only.

The Vercel entrypoint is now api/index.py (standard Vercel Python function).
This file is no longer used in production.
"""
from backend.main import app  # noqa: F401
