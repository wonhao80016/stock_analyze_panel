"""Data API: project version."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/data", tags=["data"])


@router.get("/version")
def get_version() -> dict:
    """Return the current project version defined by VERSION."""
    from app import __version__

    return {"version": f"v{__version__}"}
