"""APScheduler-based background task scheduler for Alpha."""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import Settings
from app.services.slack_service import SlackService

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def start_scheduler(slack_service: SlackService, settings: Settings) -> None:
    """Create and start the global scheduler with Slack sync jobs."""
    global _scheduler  # noqa: PLW0603

    _scheduler = AsyncIOScheduler(timezone="UTC")

    _scheduler.add_job(
        slack_service.sync_slack,
        trigger="interval",
        minutes=settings.slack_sync_minutes,
        id="sync_slack",
        name="Slack channel sync",
        replace_existing=True,
        misfire_grace_time=30,
    )

    _scheduler.add_job(
        slack_service.sync_members,
        trigger="interval",
        minutes=10,
        id="sync_members",
        name="Slack member sync",
        replace_existing=True,
        misfire_grace_time=30,
    )

    _scheduler.start()
    logger.info(
        "Scheduler started: channel sync every %d min, member sync every 10 min",
        settings.slack_sync_minutes,
    )


def stop_scheduler() -> None:
    """Shut down the global scheduler if it is running."""
    global _scheduler  # noqa: PLW0603
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
    _scheduler = None
