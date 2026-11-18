"""FastAPI dependency injection helpers for Alpha."""

from __future__ import annotations

from fastapi import Request

from app.config import Settings
from app.services.file_service import FileService
from app.services.groq_service import GroqService
from app.services.slack_service import SlackService


def get_file_service(request: Request) -> FileService:
    """Return the shared FileService from application state."""
    return request.app.state.file_service


def get_slack_service(request: Request) -> SlackService | None:
    """Return the shared SlackService, or None if Slack is not configured."""
    return getattr(request.app.state, "slack_service", None)


def get_settings_dep(request: Request) -> Settings:
    """Return the application Settings from application state."""
    return request.app.state.settings


def get_groq_service(request: Request) -> GroqService:
    """Return the shared GroqService from application state."""
    return request.app.state.groq_service
