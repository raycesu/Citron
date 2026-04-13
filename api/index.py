"""Vercel Python function entrypoint — re-exports the FastAPI ASGI app."""
from backend.main import app  # noqa: F401
