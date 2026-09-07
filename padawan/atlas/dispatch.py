"""Bounded dispatch that drains admitted work after a worker or receipt failure."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import TypeVar

T = TypeVar("T")


async def drain_dispatch[T](
    jobs: Sequence[T],
    *,
    dispatch: Callable[[T], Awaitable[dict[str, str]]],
    request_id: Callable[[T], str],
    concurrency: int,
    dispatch_deadline: datetime,
    stop: asyncio.Event | None = None,
    on_result: Callable[[T, dict[str, str]], None] | None = None,
    admission_check: Callable[[], None] | None = None,
) -> dict[str, str]:
    """No retries. Stop admission on failure; await every already-admitted coroutine.

    A failure after native result persistence remains an infrastructure receipt error here;
    canonical database results take precedence when the final analysis reconstructs outcomes.
    Cancellation of the controller drains started work before the caller closes its client/DB.
    An OS process kill still leaves unresolved native calls for explicit recovery assessment.
    """
    if concurrency < 1 or dispatch_deadline.tzinfo is None:
        raise ValueError("dispatch requires positive concurrency and an aware deadline")
    ids = [request_id(job) for job in jobs]
    if len(ids) != len(set(ids)):
        raise ValueError("a request may appear only once in a finite dispatch")
    outcomes = dict.fromkeys(ids, "not_run")
    queue: asyncio.Queue[T] = asyncio.Queue()
    for job in jobs:
        queue.put_nowait(job)
    stopping = stop or asyncio.Event()

    async def worker() -> None:
        while not stopping.is_set() and datetime.now(UTC) < dispatch_deadline:
            try:
                job = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            rid = request_id(job)
            try:
                if admission_check is not None:
                    admission_check()
                result = await dispatch(job)
                if set(result) != {rid}:
                    raise ValueError("dispatch returned a different request identity")
                outcomes.update(result)
                if any(
                    value not in {"verified_success", "verified_failure"}
                    for value in result.values()
                ):
                    stopping.set()
                if on_result is not None:
                    on_result(job, result)
            except asyncio.CancelledError:
                stopping.set()
                outcomes[rid] = "dispatch_cancelled"
                raise
            except Exception as error:
                stopping.set()
                # Never include exception text: a provider error could carry raw model traffic.
                outcomes[rid] = f"dispatch_infrastructure_failure:{type(error).__name__}"
            finally:
                queue.task_done()

    workers = [asyncio.create_task(worker()) for _ in range(min(concurrency, len(jobs)))]
    drain = asyncio.gather(*workers, return_exceptions=True)
    try:
        await asyncio.shield(drain)
    except asyncio.CancelledError:
        stopping.set()
        await asyncio.shield(drain)
        raise
    return outcomes
