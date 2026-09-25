from __future__ import annotations

from fastapi import APIRouter

from .. import __version__

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("", summary="Is nextrmnl running?")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
