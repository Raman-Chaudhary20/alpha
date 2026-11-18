from fastapi import APIRouter, Depends
from fastapi.responses import Response

from app.config import Settings
from app.dependencies import caller_scope, get_file_service, get_settings_dep, get_store
from app.services.file_service import FileService
from app.services.store import Store

router = APIRouter()


@router.get("/history", summary="Return conversation history (Markdown)")
async def get_history(
    file_service: FileService = Depends(get_file_service),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    """Return the recent contents of conversation.md."""
    content = file_service.tail(file_service.conversation_file, settings.max_conversation_chars) or "(empty)"
    return Response(content=content, media_type="text/markdown; charset=utf-8")


@router.get("/slack-data", summary="Return the latest stored Slack messages")
async def get_slack_data(
    scope: str = Depends(caller_scope),
    store: Store = Depends(get_store),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    """The latest messages, one per line. Public channels only, unless the admin key is sent."""
    content = store.recent_lines(scope, settings.max_slack_chars) or "(empty)"
    return Response(content=content, media_type="text/plain; charset=utf-8")
