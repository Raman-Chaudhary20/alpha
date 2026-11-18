from fastapi import APIRouter, Depends
from fastapi.responses import Response
from app.services.file_service import FileService
from app.config import Settings
from app.dependencies import get_file_service, get_settings_dep

router = APIRouter()


@router.get("/history", summary="Return conversation history (Markdown)")
async def get_history(
    file_service: FileService = Depends(get_file_service),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    """Return the recent contents of conversation.md."""
    content = file_service.tail(file_service.conversation_file, settings.max_conversation_chars) or "(empty)"
    return Response(content=content, media_type="text/markdown; charset=utf-8")


@router.get("/slack-data", summary="Return raw Slack message log")
async def get_slack_data(
    file_service: FileService = Depends(get_file_service),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    """Return the recent contents of slack_messages.txt."""
    content = file_service.tail(file_service.slack_file, settings.max_slack_chars) or "(empty)"
    return Response(content=content, media_type="text/plain; charset=utf-8")
