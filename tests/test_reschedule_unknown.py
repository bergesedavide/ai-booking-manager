"""M07.2B: traditional modification never queries availability."""
import asyncio
from test_legacy_writes_disabled import bot, context, update


def test_reschedule_unknown(bot):
    u=update();c=context()
    asyncio.run(bot.modifica(u,c))
    bot.authenticate.assert_not_called();bot.check_reschedule_availability_safe.assert_not_called()
    bot.update_booking.assert_not_called()
    assert 'pending_reschedules' not in c.user_data
    assert 'linguaggio naturale' in u.effective_message.reply_text.call_args.args[0]
