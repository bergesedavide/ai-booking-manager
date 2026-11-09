"""M07.2B: old deletion buttons cannot reach deterministic deletion.
ETag and HTTP error behavior remains covered by test_delete_booking.py and AI tests.
"""
import asyncio
from test_legacy_writes_disabled import bot, context, update


def test_delete_cancel_does_not_call_google(bot):
    u=update(callback=True,payload='delete_cancel:0123456789abcdef');c=context()
    asyncio.run(bot.handle_delete_callback(u,c))
    bot.authenticate.assert_not_called();bot.build.assert_not_called();bot.delete_booking.assert_not_called()
    assert 'pending_deletions' not in c.user_data
    assert 'precedente non è più disponibile' in u.callback_query.edit_message_text.call_args.args[0]


def test_delete_duplicate_confirmation_is_blocked(bot):
    u=update(callback=True,payload='delete_confirm:0123456789abcdef');c=context()
    c.user_data.pop('pending_deletions')
    asyncio.run(bot.handle_delete_callback(u,c))
    asyncio.run(bot.handle_delete_callback(u,c))
    bot.delete_booking.assert_not_called();bot.get_booking.assert_not_called()
    assert u.callback_query.edit_message_text.await_count==2


def test_delete_unauthorized_user_is_blocked(bot):
    u=update(False,True,payload='delete_confirm:0123456789abcdef');c=context();before=c.user_data.copy()
    asyncio.run(bot.handle_delete_callback(u,c))
    assert c.user_data==before
    u.callback_query.answer.assert_awaited_once_with('Non autorizzato.',show_alert=True)
    u.callback_query.edit_message_text.assert_not_awaited()
    bot.delete_booking.assert_not_called()


def test_delete_confirmation_disabled_with_existing_draft(bot):
    u=update(callback=True,payload='delete_confirm:0123456789abcdef');c=context()
    asyncio.run(bot.handle_delete_callback(u,c))
    bot.get_booking.assert_not_called();bot.delete_booking.assert_not_called()
    assert 'pending_deletions' not in c.user_data
    assert 'precedente non è più disponibile' in u.callback_query.edit_message_text.call_args.args[0]
