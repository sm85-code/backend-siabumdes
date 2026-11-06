"""Deployment feature switches. Riska requires explicit opt-in."""
import os

from fastapi import HTTPException


def riska_enabled() -> bool:
    return os.getenv("RISKA_ENABLED", "").strip().lower() == "true"


def require_riska_enabled() -> None:
    # Router dependency runs before authentication/database dependencies.
    if not riska_enabled():
        raise HTTPException(404, "Fitur tidak tersedia")
