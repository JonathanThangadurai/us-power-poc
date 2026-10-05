"""Scheduler running inside the app process (not GitHub Actions cron — unreliable at 5-min cadence)."""

from __future__ import annotations

import asyncio
import logging

from app import config
from app.ingest import ingest_dayahead_latest, ingest_realtime_latest

logger = logging.getLogger("scheduler")

_tasks: list[asyncio.Task] = []


async def _realtime_loop() -> None:
    while True:
        try:
            await ingest_realtime_latest()
        except Exception:  # noqa: BLE001 - never let the loop die
            logger.exception("realtime ingest loop iteration failed")
        await asyncio.sleep(config.REALTIME_POLL_SECONDS)


async def _dayahead_loop() -> None:
    while True:
        try:
            await ingest_dayahead_latest()
        except Exception:  # noqa: BLE001
            logger.exception("day-ahead ingest loop iteration failed")
        await asyncio.sleep(config.DAYAHEAD_POLL_SECONDS)


def start() -> None:
    if config.DISABLE_SCHEDULER:
        logger.info("Scheduler disabled via DISABLE_SCHEDULER")
        return
    _tasks.append(asyncio.create_task(_realtime_loop()))
    _tasks.append(asyncio.create_task(_dayahead_loop()))
    logger.info("Scheduler started: realtime every %ss, day-ahead every %ss",
                config.REALTIME_POLL_SECONDS, config.DAYAHEAD_POLL_SECONDS)


def stop() -> None:
    for task in _tasks:
        task.cancel()
    _tasks.clear()
