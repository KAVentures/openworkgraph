from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from typing import Any, Callable

from fastapi import FastAPI

LifecycleHook = Callable[[], Any]


async def _run_hook(hook: LifecycleHook | None) -> None:
    if hook is None:
        return
    result = hook()
    if inspect.isawaitable(result):
        await result


def extend_lifespan(
    app: FastAPI,
    *,
    startup: LifecycleHook | None = None,
    shutdown: LifecycleHook | None = None,
) -> None:
    """Compose startup/shutdown work onto an existing FastAPI lifespan.

    The existing lifespan enters first, then the new startup hook runs. Shutdown
    unwinds in reverse order. This lets additive app layers extend lifecycle
    behavior without deprecated ``app.on_event`` handlers or replacing earlier
    initialization logic.
    """

    previous = app.router.lifespan_context

    @asynccontextmanager
    async def combined(app_instance: FastAPI):
        async with previous(app_instance) as state:
            await _run_hook(startup)
            try:
                yield state
            finally:
                await _run_hook(shutdown)

    app.router.lifespan_context = combined
