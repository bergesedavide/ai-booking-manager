"""M07.2B: traditional booking stops before availability or Google errors."""
import asyncio
from test_legacy_writes_disabled import bot, context, update


def test_google_availability_error(bot):
    u=update();c=context()
    asyncio.run(bot.prenota(u,c))
    bot.authenticate.assert_not_called();bot.check_availability.assert_not_called()
    bot.create_booking.assert_not_called()
    assert 'linguaggio naturale' in u.effective_message.reply_text.call_args.args[0]


def test_google_creation_error(bot):
    u=update();c=context();c.args=['2026-11-25','16:00','30','Cliente fittizio']
    asyncio.run(bot.prenota(u,c))
    bot.build.assert_not_called();bot.create_booking.assert_not_called()
    assert 'pending_bookings' not in c.user_data
