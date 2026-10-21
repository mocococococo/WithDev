"""Run alongside the API: python -m app.workers.task_assignment."""
import asyncio
import logging
import signal

from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import engine
from app.services.task_assignment_service import (
    dispatch_next_assignment, expire_assignments, process_next_event, select_next_assignment,
)

logger = logging.getLogger(__name__)


async def _wait_for_stop(stop_event: asyncio.Event, timeout: float):
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=timeout)
    except TimeoutError:
        pass


async def run(stop_event: asyncio.Event | None = None):
    settings = get_settings()
    if not settings.slack_signing_secret:
        raise RuntimeError("SLACK_SIGNING_SECRET is required for the assignment worker")
    if not settings.gemini_api_key or not settings.gemini_model:
        raise RuntimeError("GEMINI_API_KEY and GEMINI_MODEL are required for the assignment worker")
    stop_event = stop_event or asyncio.Event()
    lock_connection = None
    acquired = False
    try:
        lock_connection = await engine.connect()
        # During a rolling deployment the new revision waits for the old leader to stop.
        while not stop_event.is_set():
            acquired = await lock_connection.scalar(text("SELECT pg_try_advisory_lock(87214301)"))
            await lock_connection.commit()
            if acquired:
                break
            logger.info("Waiting for the active assignment worker to release its lock")
            await _wait_for_stop(stop_event, 5)
        if not acquired:
            return
        logger.info("Assignment worker acquired leadership")
        while not stop_event.is_set():
            # Stop if the dedicated lock connection is lost, rather than continuing without exclusivity.
            await lock_connection.execute(text("SELECT 1"))
            await lock_connection.commit()
            try:
                # Drain callbacks first; one worker preserves the order of each conversation.
                for _ in range(100):
                    if stop_event.is_set() or not await process_next_event():
                        break
                if stop_event.is_set():
                    break
                await expire_assignments()
                if stop_event.is_set():
                    break
                await select_next_assignment()
                if not stop_event.is_set():
                    await dispatch_next_assignment()
            except Exception:
                logger.exception("Assignment worker iteration failed")
            await _wait_for_stop(stop_event, 1)
    finally:
        try:
            if lock_connection is not None:
                if acquired:
                    try:
                        await lock_connection.execute(text("SELECT pg_advisory_unlock(87214301)"))
                        await lock_connection.commit()
                    except Exception:
                        # Never return a connection with an uncertain session lock to the pool.
                        await lock_connection.invalidate()
                await lock_connection.close()
        finally:
            await engine.dispose()


async def main():
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop_event.set)
    try:
        await run(stop_event)
    finally:
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(signum)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
