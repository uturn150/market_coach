"""Thin re-export so the shared site manager's `uvicorn app:app` convention
works — the real implementation lives in webapp.py (kept as the descriptive
name since this project already has a bunch of same-purpose `*_portfolio.py`
runner scripts and 'app' alone would be ambiguous among them)."""
from webapp import app  # noqa: F401
