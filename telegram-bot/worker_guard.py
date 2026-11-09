"""Wait for a write-capable worker before propagating handler cancellation."""
import asyncio


async def await_worker(function, /, *args, **kwargs):
    worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(worker)
        except asyncio.CancelledError:
            if worker.cancelled():
                raise
            cancelled = True
            continue  # Repeated handler cancellations must not abandon the worker.
        except Exception:
            if cancelled:
                raise asyncio.CancelledError() from None
            raise
        else:
            if cancelled:
                raise asyncio.CancelledError()
            return result
