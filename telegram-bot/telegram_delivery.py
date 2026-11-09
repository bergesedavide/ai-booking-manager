"""Telegram delivery never changes a persisted operation outcome or retries."""


async def deliver(send, *args, **kwargs):
    try:
        await send(*args, **kwargs)
        return True
    except Exception:
        # CancelledError is a BaseException and must propagate to worker guards.
        print('Consegna Telegram non confermata; nessun tentativo automatico.')
        return False


async def deliver_session(context, key, session, send, *args, **kwargs):
    delivered = await deliver(send, *args, **kwargs)
    if not delivered and context.user_data.get(key) is session:
        context.user_data.pop(key, None)
    return delivered
