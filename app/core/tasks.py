"""Fire-and-forget background work that must not be lost or go unnoticed."""

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

log = logging.getLogger("app.tasks")
_tasks: set[asyncio.Task] = set()


def spawn(coro: Coroutine[Any, Any, Any], name: str = "task") -> asyncio.Task:
    """Runs a coroutine in the background. Keeps a reference so it is not
    garbage-collected mid-run, and logs any exception instead of hiding it."""
    task = asyncio.get_running_loop().create_task(coro, name=name)
    _tasks.add(task)

    def _done(finished: asyncio.Task) -> None:
        _tasks.discard(finished)
        if not finished.cancelled() and finished.exception() is not None:
            log.error("Background task %s failed", name, exc_info=finished.exception())

    task.add_done_callback(_done)
    return task


async def drain(timeout: float = 30.0) -> None:  # noqa: ASYNC109 - a wait budget, not a cancel scope
    """Waits for background work to finish. Used on shutdown and in tests."""
    deadline = asyncio.get_running_loop().time() + timeout
    while _tasks and asyncio.get_running_loop().time() < deadline:
        await asyncio.wait(list(_tasks), timeout=0.2)


async def cancel_all() -> None:
    for task in list(_tasks):
        task.cancel()
    if _tasks:
        await asyncio.gather(*_tasks, return_exceptions=True)
