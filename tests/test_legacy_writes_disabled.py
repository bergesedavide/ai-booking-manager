"""Offline tests of disabled Telegram entrypoints; no bot or services started."""
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import pytest

ROOT=Path(__file__).resolve().parents[1]
COMMANDS=('prenota','modifica','cancella')
CALLBACKS=('handle_booking_callback','handle_reschedule_callback','handle_delete_callback')
PREFIXES=('booking','reschedule','delete')
LEGACY=('pending_bookings','pending_reschedules','pending_deletions')
AI=('pending_ai_bookings','ai_move','ai_delete')


@pytest.fixture
def bot(monkeypatch):
    spec=importlib.util.spec_from_file_location('legacy_disabled_bot',ROOT/'telegram-bot/bot.py')
    module=importlib.util.module_from_spec(spec)
    original=Path.read_text
    def read(path,*args,**kwargs):
        if path==ROOT/'.env':
            return 'TELEGRAM_BOT_TOKEN=fake-token\nTELEGRAM_ALLOWED_USER_ID=123\nGOOGLE_TEST_CALENDAR_ID=test-calendar\nPOSTGRES_PASSWORD=fake-password\n'
        return original(path,*args,**kwargs)
    with patch.object(Path,'read_text',read):
        spec.loader.exec_module(module)
    for name in ('authenticate','build','create_booking','update_booking','delete_booking',
                 'get_booking','check_availability','check_reschedule_availability_safe'):
        monkeypatch.setattr(module,name,Mock(side_effect=AssertionError('Service forbidden in legacy handler')))
    return module


def update(allowed=True,callback=False,payload='booking_confirm:0123456789abcdef'):
    return SimpleNamespace(effective_user=SimpleNamespace(id=123 if allowed else 456),
        effective_message=SimpleNamespace(reply_text=AsyncMock()),
        callback_query=SimpleNamespace(data=payload,answer=AsyncMock(),edit_message_text=AsyncMock()) if callback else None)


def context():
    return SimpleNamespace(args=['event-id','2026-11-25','16:00','30'],user_data={
        **{key:{'0123456789abcdef':{'event_id':'external','expected_etag':'v1'}} for key in LEGACY},
        **{key:object() for key in AI},'read_only_state':object()})


@pytest.mark.parametrize('name',COMMANDS)
def test_authorized_commands_disabled_and_residual_state_cleared(bot,name):
    u=update();c=context();preserved={key:c.user_data[key] for key in (*AI,'read_only_state')}
    asyncio.run(getattr(bot,name)(u,c))
    assert all(key not in c.user_data for key in LEGACY)
    assert all(c.user_data[key] is value for key,value in preserved.items())
    assert 'linguaggio naturale' in u.effective_message.reply_text.call_args.args[0]


@pytest.mark.parametrize('name',COMMANDS)
def test_unauthorized_commands_silent_and_do_not_change_state(bot,name):
    u=update(False);c=context();before=c.user_data.copy()
    asyncio.run(getattr(bot,name)(u,c))
    u.effective_message.reply_text.assert_not_awaited()
    assert c.user_data==before


@pytest.mark.parametrize('name,prefix',tuple(zip(CALLBACKS,PREFIXES)))
@pytest.mark.parametrize('suffix',['confirm:0123456789abcdef','confirm','confirm:obsolete','cancel:0123456789abcdef'])
def test_old_callbacks_never_reach_services(bot,name,prefix,suffix):
    u=update(callback=True,payload=f'{prefix}_{suffix}');c=context()
    preserved={key:c.user_data[key] for key in AI}
    asyncio.run(getattr(bot,name)(u,c))
    assert all(key not in c.user_data for key in LEGACY)
    assert all(c.user_data[key] is value for key,value in preserved.items())
    assert 'precedente non è più disponibile' in u.callback_query.edit_message_text.call_args.args[0]


@pytest.mark.parametrize('name',CALLBACKS)
def test_unauthorized_callbacks_no_information(bot,name):
    u=update(False,True);c=context();before=c.user_data.copy()
    asyncio.run(getattr(bot,name)(u,c))
    u.callback_query.edit_message_text.assert_not_awaited()
    assert u.callback_query.answer.call_args.args[0]=='Non autorizzato.'
    assert c.user_data==before


@pytest.mark.parametrize('name',CALLBACKS)
def test_old_buttons_repeated_after_state_removal_still_disabled(bot,name):
    u=update(callback=True);c=context()
    asyncio.run(getattr(bot,name)(u,c))
    asyncio.run(getattr(bot,name)(u,c))
    assert u.callback_query.edit_message_text.await_count==2
    assert all(key not in c.user_data for key in LEGACY)


@pytest.mark.parametrize('name',COMMANDS)
def test_missing_user_no_response(bot,name):
    u=update();u.effective_user=None
    asyncio.run(getattr(bot,name)(u,context()))
    u.effective_message.reply_text.assert_not_awaited()


def test_start_still_available(bot):
    u=update();c=context();before=c.user_data.copy()
    asyncio.run(bot.start(u,c))
    assert 'AI Booking Manager attivo' in u.effective_message.reply_text.call_args.args[0]
    assert c.user_data==before


def test_start_unauthorized_silent(bot):
    u=update(False)
    asyncio.run(bot.start(u,context()))
    u.effective_message.reply_text.assert_not_awaited()


@pytest.fixture
def registration(bot,monkeypatch):
    application=Mock()
    builder=Mock();builder.token.return_value=builder;builder.build.return_value=application
    monkeypatch.setattr(bot.Application,'builder',Mock(return_value=builder))
    # Registration only: run_polling is a Mock, never starts an actual bot.
    bot.main()
    return [call.args[0] for call in application.add_handler.call_args_list]


@pytest.mark.parametrize('prefix,name',tuple(zip(PREFIXES,CALLBACKS)))
@pytest.mark.parametrize('suffix',['confirm','confirm:0123456789abcdef','confirm:bad','cancel','cancel:expired'])
def test_registered_routes_catch_all_old_buttons(bot,registration,prefix,name,suffix):
    payload=f'{prefix}_{suffix}'
    matching=[handler for handler in registration if getattr(handler,'pattern',None) and handler.pattern.match(payload)]
    assert len(matching)==1 and matching[0].callback is getattr(bot,name)
    asyncio.run(matching[0].callback(update(callback=True,payload=payload),context()))


def test_registered_commands_keep_disabled_entrypoints(bot,registration):
    commands={command:handler.callback for handler in registration for command in getattr(handler,'commands',())}
    assert commands['start'] is bot.start
    for name in COMMANDS:assert commands[name] is getattr(bot,name)


@pytest.mark.parametrize('payload,expected',[
    ('ai_confirm:0123456789abcdef','ai_booking_callback'),
    ('ai_overlap:0123456789abcdef','ai_booking_callback'),
    ('aimove:confirm:0123456789abcdef:0','ai_move_callback'),
    ('aidelete:confirm:0123456789abcdef:0','ai_delete_callback'),
])
def test_ai_callback_routes_not_captured_by_legacy(registration,payload,expected):
    matching=[handler for handler in registration if getattr(handler,'pattern',None) and handler.pattern.match(payload)]
    assert len(matching)==1 and matching[0].callback.__name__==expected


def test_text_dispatch_ignores_residual_traditional_state(bot,registration,monkeypatch):
    dispatch=next(handler.callback for handler in registration if handler.callback.__name__=='ai_message_handler')
    ai=AsyncMock();monkeypatch.setattr(bot,'handle_ai_message',ai)
    u=update();c=context()
    asyncio.run(dispatch(u,c))
    ai.assert_awaited_once_with(u,c,123,'test-calendar','fake-password')
