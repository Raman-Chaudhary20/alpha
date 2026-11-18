"""FastAPI dependency injection helpers for Alpha."""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request

from app.config import Settings
from app.core.secrets import Secrets
from app.services.bedrock_service import Brain
from app.services.file_service import FileService
from app.services.harvest_service import Harvester
from app.services.memory_service import MemoryBuilder
from app.services.slack_service import SlackService
from app.services.store import Store


def get_file_service(request: Request) -> FileService:
    return request.app.state.file_service


def get_slack_service(request: Request) -> SlackService | None:
    return getattr(request.app.state, "slack_service", None)


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_brain(request: Request) -> Brain:
    return request.app.state.brain


def get_store(request: Request) -> Store:
    return request.app.state.store


def get_harvester(request: Request) -> Harvester:
    return request.app.state.harvester


def get_memory(request: Request) -> MemoryBuilder:
    return request.app.state.memory


def get_secrets(request: Request) -> Secrets:
    return request.app.state.secrets


def is_admin_caller(request: Request) -> bool:
    """A REST caller is an admin when API_KEY is set and the request carries it."""
    key = request.app.state.settings.api_key
    given = request.headers.get("x-api-key", "")
    return bool(key) and hmac.compare_digest(key.encode(), given.encode())


def caller_scope(request: Request) -> str:
    return "all" if is_admin_caller(request) else "public"


def require_admin(request: Request) -> None:
    if not is_admin_caller(request):
        raise HTTPException(status_code=403, detail="This needs the admin API key in the X-API-Key header.")
