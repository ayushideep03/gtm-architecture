"""Vercel entry point: the whole Oxygen API as one Python (ASGI) serverless function.

vercel.json rewrites /api/* and /health* here; the request keeps its original path, so
FastAPI routes it as usual. The dashboard is served separately as static files.
"""

from app.main import app  # noqa: F401  (Vercel looks for the ASGI app named `app`)
