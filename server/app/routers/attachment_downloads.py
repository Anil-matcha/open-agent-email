"""Short-lived signed URLs for downloading private message attachments."""
import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Message, MessageAttachment
from app.services.attachment_utils import verify_attachment_download_signature

router = APIRouter(tags=["Attachments"])


@router.get("/v1/attachments/{message_id}/{attachment_id}/download")
async def download_signed_attachment(
    message_id: str,
    attachment_id: str,
    expires: int = Query(..., ge=1),
    signature: str = Query(..., min_length=64, max_length=64),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(MessageAttachment, Message.agent_email)
        .join(Message, Message.id == MessageAttachment.message_id)
        .where(Message.id == message_id, MessageAttachment.id == attachment_id)
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    attachment, agent_email = row
    try:
        valid = verify_attachment_download_signature(agent_email, message_id, attachment_id, expires, signature)
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Attachment download signing is not configured") from None
    if not valid:
        raise HTTPException(status_code=404, detail="Attachment not found")
    filename = attachment.filename.replace('"', "_").replace("\\", "_").replace("\r", "_").replace("\n", "_")
    return Response(
        content=attachment.content,
        media_type=attachment.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )
