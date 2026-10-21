"""Run alongside the API: python -m app.workers.task_assignment."""
import asyncio
import logging

from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import engine
from app.services.task_assignment_service import (
    dispatch_next_assignment, expire_assignments, process_next_event, select_next_assignment,
)

logger = logging.getLogger(__name__)


async def run():
    settings = get_settings()
    if not settings.slack_signing_secret:
        raise RuntimeError("SLACK_SIGNING_SECRET is required for the assignment worker")
    # One active worker preserves callback order. A crashed process releases this session lock.
    lock_connection = await engine.connect()
    acquired = await lock_connection.scalar(text("SELECT pg_try_advisory_lock(87214301)"))
    await lock_connection.commit()
    if not acquired:
        await lock_connection.close()
        raise RuntimeError("An assignment worker is already running")
    try:
        while True:
            # Stop if the dedicated lock connection is lost, rather than continuing without exclusivity.
            await lock_connection.execute(text("SELECT 1"))
            await lock_connection.commit()
            try:
                # Drain callbacks first; one worker preserves the order of each conversation.
                for _ in range(100):
                    if not await process_next_event():
                        break
                await expire_assignments()
                await select_next_assignment()
                await dispatch_next_assignment()
            except Exception:
                logger.exception("Assignment worker iteration failed")
            await asyncio.sleep(1)
    finally:
        await lock_connection.execute(text("SELECT pg_advisory_unlock(87214301)"))
        await lock_connection.commit()
        await lock_connection.close()
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
