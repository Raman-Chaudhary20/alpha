import time

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_store
from app.models.schemas import MessageRequest, MessageResponse
from app.services.store import Store

router = APIRouter()


@router.post("/message", response_model=MessageResponse, summary="Save a message to Alpha's memory")
async def save_message(
    body: MessageRequest | None = None,
    store: Store = Depends(get_store),
) -> MessageResponse:
    """Store an arbitrary message as if posted in a public channel (useful for testing)."""
    if body is None:
        raise HTTPException(status_code=400, detail="text is required")

    text = (body.text or body.message or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    conversation_id = f"api-{body.channel}"
    if not store.conversation(conversation_id):
        store.upsert_conversation(conversation_id, "public", f"#{body.channel} (API)", [])
    store.upsert_message(conversation_id, f"{time.time():.6f}", body.user, text)
    return MessageResponse(saved=True)
