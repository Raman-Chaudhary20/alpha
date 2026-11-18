"""APScheduler-based background task scheduler for Alpha."""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import Settings
from app.services.memory_service import MemoryBuilder
from app.services.slack_service import SlackService

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def start_scheduler(slack_service: SlackService, memory: MemoryBuilder, settings: Settings) -> None:
    """Read Slack often, people every 10 minutes, and rebuild the memory on its own clock."""
    global _scheduler  # noqa: PLW0603

    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        slack_service.sync_slack,
        trigger="interval",
        minutes=settings.slack_sync_minutes,
        id="sync_slack",
        name="Read every reachable Slack conversation",
        replace_existing=True,
        misfire_grace_time=30,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        slack_service.sync_members,
        trigger="interval",
        minutes=10,
        id="sync_members",
        name="Slack people",
        replace_existing=True,
        misfire_grace_time=30,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        memory.build,
        trigger="interval",
        minutes=settings.memory_build_minutes,
        id="build_memory",
        name="Day summaries and the register of projects and people",
        replace_existing=True,
        misfire_grace_time=120,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    logger.info(
        "Scheduler started: Slack every %d min, people every 10 min, memory every %d min",
        settings.slack_sync_minutes,
        settings.memory_build_minutes,
    )


def stop_scheduler() -> None:
    global _scheduler  # noqa: PLW0603
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
    _scheduler = None
