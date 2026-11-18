"""Alpha FastAPI application factory and lifespan management."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.api.v1.router import root_router
from app.api.v1.router import router as v1_router
from app.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import setup_logging
from app.core.secrets import Secrets
from app.services.bedrock_service import Bedrock, Brain
from app.services.file_service import FileService
from app.services.harvest_service import Harvester, make_client
from app.services.memory_service import MemoryBuilder
from app.services.slack_service import SlackService
from app.services.store import Store
from app.tasks.scheduler import start_scheduler, stop_scheduler

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Create the services (unless already provided, as tests do) and start Slack."""
    settings: Settings = getattr(app.state, "settings", None) or get_settings()
    state = app.state

    setup_logging(level="DEBUG" if settings.debug else "INFO")
    logger.info("Starting %s v%s", settings.app_name, settings.app_version)

    if getattr(state, "file_service", None) is None:
        state.file_service = FileService(data_dir=settings.data_dir)
    state.file_service.ensure_data_dir()
    if getattr(state, "store", None) is None:
        state.store = Store(Path(settings.data_dir) / "alpha.db")
    if getattr(state, "secrets", None) is None:
        state.secrets = Secrets(settings.token_key)
    if getattr(state, "bedrock", None) is None:
        state.bedrock = Bedrock(settings)
    if getattr(state, "brain", None) is None:
        state.brain = Brain(settings, state.store, state.bedrock)
    if getattr(state, "memory", None) is None:
        state.memory = MemoryBuilder(settings, state.store, state.bedrock)

    has_slack = bool(settings.slack_bot_token and settings.slack_app_token)
    bot_client = make_client(settings.slack_bot_token) if has_slack else None
    if getattr(state, "harvester", None) is None:
        state.harvester = Harvester(settings, state.store, state.secrets, bot_client)

    slack_service: SlackService | None = getattr(state, "slack_service", None)
    if has_slack and slack_service is None:
        slack_service = SlackService(
            settings=settings,
            file_service=state.file_service,
            store=state.store,
            brain=state.brain,
            harvester=state.harvester,
            secrets=state.secrets,
            bot_client=bot_client,
        )
        state.slack_service = slack_service

        async def _start_slack_background() -> None:
            try:
                await slack_service.start()
                start_scheduler(slack_service=slack_service, memory=state.memory, settings=settings)
                logger.info("Slack integration active")
            except Exception:
                logger.exception("Failed to start Slack integration; running without Slack")

        asyncio.create_task(_start_slack_background())
    elif not has_slack:
        state.slack_service = None
        logger.warning("SLACK_BOT_TOKEN / SLACK_APP_TOKEN not set — Slack features disabled")

    yield

    logger.info("Shutting down %s", settings.app_name)
    stop_scheduler()
    if slack_service:
        await slack_service.stop()
    logger.info("Shutdown complete")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Application factory used by uvicorn and tests alike."""
    if settings is None:
        settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "Alpha, the organization's Slack memory: reads every conversation it is allowed to "
            "(channels, and the DMs of people who sign in), removes repeats, keeps a register of "
            "projects and people with Claude on Amazon Bedrock, and answers questions."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    app.state.settings = settings
    register_exception_handlers(app)
    app.include_router(v1_router)
    app.include_router(root_router)

    @app.get("/", tags=["Health"], summary="Health check")
    async def health_check() -> JSONResponse:
        return JSONResponse(content={"status": "ok", "app": settings.app_name, "version": settings.app_version})

    return app


app = create_app()
