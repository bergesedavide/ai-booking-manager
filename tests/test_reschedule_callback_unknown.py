"""M07.2B: old reschedule callbacks are rejected before any Google access."""
import asyncio
from test_legacy_writes_disabled import bot, context, update


def test_callback_unknown(bot):
    u=update(callback=True,payload='reschedule_confirm:0123456789abcdef');c=context()
    asyncio.run(bot.handle_reschedule_callback(u,c))
    bot.authenticate.assert_not_called();bot.build.assert_not_called()
    bot.check_reschedule_availability_safe.assert_not_called();bot.update_booking.assert_not_called()
    assert 'pending_reschedules' not in c.user_data
    assert 'precedente non è più disponibile' in u.callback_query.edit_message_text.call_args.args[0]
