"""Alpha FastAPI application factory and lifespan management."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.api.v1.router import router as v1_router, root_router
from app.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import setup_logging
from app.services.file_service import FileService
from app.services.groq_service import GroqService
from app.services.slack_service import SlackService
from app.tasks.scheduler import start_scheduler, stop_scheduler

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage startup and shutdown of background services."""
    settings: Settings = getattr(app.state, "settings", None) or get_settings()

    # --- Startup ---
    setup_logging(level="DEBUG" if settings.debug else "INFO")
    logger.info("Starting %s v%s", settings.app_name, settings.app_version)

    file_service = FileService(data_dir=settings.data_dir)
    file_service.ensure_data_dir()
    app.state.file_service = file_service

    groq_service = GroqService(settings=settings, file_service=file_service)
    app.state.groq_service = groq_service

    slack_service: SlackService | None = None
    if settings.slack_bot_token and settings.slack_app_token:
        slack_service = SlackService(
            settings=settings,
            file_service=file_service,
            groq_service=groq_service,
        )
        app.state.slack_service = slack_service

        async def _start_slack_background() -> None:
            try:
                await slack_service.start()
                start_scheduler(slack_service=slack_service, settings=settings)
                logger.info("Slack integration active")
            except Exception:
                logger.exception("Failed to start Slack integration; running without Slack")

        asyncio.create_task(_start_slack_background())
    else:
        app.state.slack_service = None
        logger.warning(
            "SLACK_BOT_TOKEN / SLACK_APP_TOKEN not set — Slack features disabled"
        )

    yield

    # --- Shutdown ---
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
            "Groq-powered Slack AI assistant that syncs workspace messages "
            "and answers questions via @mention or REST API."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # Attach settings early so lifespan and deps can read them
    app.state.settings = settings

    # Exception handlers
    register_exception_handlers(app)

    # Routers
    app.include_router(v1_router)
    app.include_router(root_router)

    # Health check
    @app.get("/", tags=["Health"], summary="Health check")
    async def health_check() -> JSONResponse:
        return JSONResponse(
            content={"status": "ok", "app": settings.app_name, "version": settings.app_version}
        )

    return app


# Top-level app instance for uvicorn
app = create_app()
