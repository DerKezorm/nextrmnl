"""Error answers with a code.

The backend does not translate, it names. Every error carries a code; the frontend builds the sentence from
``errors.byCode`` in its language files. The English text is the fallback for anyone using the API directly.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException


def meldung(code: str, text: str, **values: Any) -> dict[str, Any]:
    return {"code": code, "message": text, **values}


def fehler(code: str, text: str, status_code: int = 400, **values: Any) -> HTTPException:
    return HTTPException(status_code=status_code, detail=meldung(code, text, **values))
