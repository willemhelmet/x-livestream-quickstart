"""Stop a studio-owned model worker when its controlling process disappears."""

import asyncio
import logging
import os

logger = logging.getLogger(__name__)


def parent_id():
    value = os.environ.get('STUDIO_PARENT_PID')
    if value is None:
        return None  # Explicit standalone CLI workers have no studio owner.
    if not value.isdecimal() or int(value) <= 1:
        raise SystemExit('Invalid studio parent PID; refusing to start generation.')
    return int(value)


async def watch_parent(expected, stopped):
    while not stopped.is_set():
        if os.getppid() != expected:
            logger.warning('Studio process exited; stopping generation and local encoder.')
            stopped.set()
            return
        await asyncio.sleep(0.5)
