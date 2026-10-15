"""Administrator view of server settings and a setup readiness checklist."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.security import Principal, require_admin
from app.services import server_settings

router = APIRouter(prefix="/v1/settings", tags=["Settings"], dependencies=[Depends(require_admin)])


class ServerSettingsUpdate(BaseModel):
    values: dict[str, Any] = Field(..., description="Setting key to new value. null removes the saved value so the environment default applies again.")


async def apply_update(db: AsyncSession, values: dict[str, Any], updated_by: str) -> list[dict[str, Any]]:
    if not values:
        raise HTTPException(status_code=422, detail="Provide at least one setting in values")
    cleaned, errors = server_settings.validate_updates(values)
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(f"{e['key']}: {e['message']}" for e in errors))
    await server_settings.save_updates(db, cleaned, updated_by)
    return await server_settings.describe(db)


@router.get("")
async def list_server_settings(db: AsyncSession = Depends(get_db)):
    return {"items": await server_settings.describe(db)}


@router.put("")
async def update_server_settings(body: ServerSettingsUpdate, principal: Principal = Depends(require_admin),
                                 db: AsyncSession = Depends(get_db)):
    return {"items": await apply_update(db, body.values, principal.principal_id or "admin")}


@router.get("/readiness")
async def server_readiness(db: AsyncSession = Depends(get_db)):
    return await server_settings.readiness(db)
