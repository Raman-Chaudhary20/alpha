from fastapi import APIRouter, Depends, HTTPException
from app.models.schemas import SyncResponse
from app.services.slack_service import SlackService
from app.dependencies import get_slack_service

router = APIRouter()


@router.post("/sync", response_model=SyncResponse, summary="Force an immediate Slack sync")
async def force_sync(
    slack_service: SlackService | None = Depends(get_slack_service),
) -> SyncResponse:
    """Trigger an immediate channel sync and member sync.

    Returns HTTP 400 if Slack credentials are not configured.
    """
    if slack_service is None:
        raise HTTPException(
            status_code=400,
            detail="Slack is not configured on this instance. Set SLACK_BOT_TOKEN and SLACK_APP_TOKEN.",
        )
    # Invalidate channel cache so a fresh list is fetched
    await slack_service.list_channels(force=True)
    await slack_service.sync_slack()
    await slack_service.sync_members()
    return SyncResponse(synced=True)
