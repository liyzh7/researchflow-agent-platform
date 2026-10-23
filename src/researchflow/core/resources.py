"""A timed-out CPU thread still holds its permit until it actually completes."""

import asyncio

_workers = set()


async def run_bounded_thread(slots, operation, timeout_seconds):
    async with asyncio.timeout(timeout_seconds):
        await slots.acquire()
        try:
            worker = asyncio.create_task(asyncio.to_thread(operation))
            _workers.add(worker)
        except BaseException:
            slots.release()
            raise

        def finished(task):
            _workers.discard(task)
            slots.release()
            # Retrieve late exceptions after a caller timeout without logging private inputs.
            if not task.cancelled():
                task.exception()

        worker.add_done_callback(finished)
        return await asyncio.shield(worker)


async def drain_workers(timeout_seconds=5):
    pending = [task for task in _workers if task.get_loop() is asyncio.get_running_loop()]
    if not pending:
        return 0
    _, unfinished = await asyncio.wait(pending, timeout=timeout_seconds)
    return len(unfinished)
