from fastapi import APIRouter, Depends, HTTPException
from app.models.schemas import MessageRequest, MessageResponse
from app.services.file_service import FileService
from app.dependencies import get_file_service
from datetime import datetime, timezone

router = APIRouter()


@router.post("/message", response_model=MessageResponse, summary="Save a message to the log")
async def save_message(
    body: MessageRequest | None = None,
    file_service: FileService = Depends(get_file_service),
) -> MessageResponse:
    """Persist an arbitrary message to slack_messages.txt (useful for testing)."""
    if body is None:
        raise HTTPException(status_code=400, detail="text is required")

    text = (body.text or body.message or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    dt = datetime.now(tz=timezone.utc)
    file_service.append_slack_line(dt, body.channel, body.user, text)
    return MessageResponse(saved=True)
