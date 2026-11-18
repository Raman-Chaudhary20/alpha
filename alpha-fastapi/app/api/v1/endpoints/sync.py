import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_memory, get_slack_service
from app.models.schemas import SyncResponse
from app.services.memory_service import MemoryBuilder
from app.services.slack_service import SlackService

router = APIRouter()


@router.post("/sync", response_model=SyncResponse, summary="Read Slack now and rebuild the memory")
async def force_sync(
    slack_service: SlackService | None = Depends(get_slack_service),
    memory: MemoryBuilder = Depends(get_memory),
) -> SyncResponse:
    """Read people and every reachable conversation now, then rebuild the memory in the background.

    Returns HTTP 400 if Slack credentials are not configured.
    """
    if slack_service is None:
        raise HTTPException(
            status_code=400,
            detail="Slack is not configured on this instance. Set SLACK_BOT_TOKEN and SLACK_APP_TOKEN.",
        )
    await slack_service.sync_members()
    await slack_service.sync_slack()
    asyncio.create_task(memory.build())
    return SyncResponse(synced=True)
