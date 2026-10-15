from typing import Optional

from fastapi import HTTPException


def merge_page_token(cursor: Optional[str], page_token: Optional[str]) -> Optional[str]:
    """`page_token` is an alias for `cursor`; supplying both is fine only when they match."""
    if not isinstance(page_token, str):  # MCP calls route functions directly, leaving the Query default
        return cursor
    if cursor and page_token and cursor != page_token:
        raise HTTPException(status_code=400, detail={"code": "invalid_cursor", "message": "cursor and page_token must match when both are provided"})
    return cursor or page_token
