"""M07.2B: historical booking buttons are disabled, including cancellation."""
import asyncio
from test_legacy_writes_disabled import bot, context, update


def test_booking_cancel(bot):
    u=update(callback=True,payload='booking_cancel:0123456789abcdef');c=context()
    asyncio.run(bot.handle_booking_callback(u,c))
    bot.authenticate.assert_not_called();bot.create_booking.assert_not_called()
    assert 'pending_bookings' not in c.user_data
    assert 'precedente non è più disponibile' in u.callback_query.edit_message_text.call_args.args[0]


def test_booking_confirm(bot):
    u=update(callback=True);c=context()
    asyncio.run(bot.handle_booking_callback(u,c))
    bot.authenticate.assert_not_called();bot.build.assert_not_called();bot.create_booking.assert_not_called()
    assert 'pending_bookings' not in c.user_data
    assert 'precedente non è più disponibile' in u.callback_query.edit_message_text.call_args.args[0]


def test_unauthorized_callback(bot):
    u=update(False,True);c=context();before=c.user_data.copy()
    asyncio.run(bot.handle_booking_callback(u,c))
    assert c.user_data==before
    u.callback_query.edit_message_text.assert_not_awaited()
    u.callback_query.answer.assert_awaited_once_with('Non autorizzato.',show_alert=True)
