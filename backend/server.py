"""
Vercel entrypoint shim.

Vercel's FastAPI runtime auto-discovers the ASGI app from standard filenames
(app.py, index.py, server.py).  The actual application lives in main.py, so
this file re-exports `app` under a name Vercel will find.
"""
from backend.main import app  # noqa: F401
