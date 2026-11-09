"""Offline M07.2A tests. Fake DB/AI/Google service, no threads or sockets."""
import asyncio
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'telegram-bot'))
import ai_handler as h
from ai.schemas import AppointmentIntent
from booking_service import BookingResult, BookingStatus

INTENT=AppointmentIntent('crea','Cliente fittizio','2026-11-20','15:00',30)
CALENDAR='test-calendar'


def update(chat=2,user=1,message=3,text='Prenota'):
    return SimpleNamespace(effective_user=SimpleNamespace(id=user),effective_chat=SimpleNamespace(id=chat),
        effective_message=SimpleNamespace(chat_id=chat,message_id=message,text=text,reply_text=AsyncMock()),
        callback_query=SimpleNamespace(data='',answer=AsyncMock(),edit_message_text=AsyncMock()))


@pytest.fixture
def env(monkeypatch):
    state=SimpleNamespace(row=None,registrations=0,writes=0,markers=0,result=BookingStatus.CREATED,
        force=[],calls=[],availability=h.AvailabilityStatus.AVAILABLE)
    @contextmanager
    def connection(_):yield object()
    def register(*args):
        state.calls.append('register');state.registrations+=1
        if state.row:return h.RegistrationResult.ALREADY_EXISTS
        state.row=('PENDING','a'*32)
        return h.RegistrationResult.CREATED
    def mark_created(connection,key,event):state.row=('CREATED',event);state.calls.append('created');return True
    def mark_unknown(*args):state.row=('UNKNOWN','a'*32);return True
    def mark_failed(*args):state.row=('FAILED','a'*32);return True
    def marker(*args):state.markers+=1
    def process(*args,**kwargs):
        state.calls.append('process');state.force.append(kwargs.get('force_booking',False))
        assert state.row==('PENDING','a'*32)
        kwargs['before_google_write']()
        state.writes+=1
        return BookingResult(state.result,'a'*32 if state.result==BookingStatus.CREATED else None)
    async def inline(function,*args,**kwargs):return function(*args,**kwargs)
    monkeypatch.setattr(h.asyncio,'to_thread',inline)
    monkeypatch.setattr(h,'get_connection',connection)
    monkeypatch.setattr(h,'get_booking_operation',lambda *args:state.row)
    monkeypatch.setattr(h,'register_ai_booking',register)
    monkeypatch.setattr(h,'mark_booking_created',mark_created)
    monkeypatch.setattr(h,'mark_booking_unknown',mark_unknown)
    monkeypatch.setattr(h,'mark_booking_failed',mark_failed)
    monkeypatch.setattr(h,'record_google_write_start',marker)
    monkeypatch.setattr(h,'process_booking',process)
    monkeypatch.setattr(h,'check_ai_availability',lambda *args:state.availability)
    monkeypatch.setattr(h,'conversations',{})
    monkeypatch.setattr(h,'conversation_locks',asyncio.Lock())
    return state


def run(coro):return asyncio.run(coro)


def prepare(u,c,intent=INTENT,key='ai:2:3'):
    run(h.send_ai_result(u,c,intent,CALENDAR,key,'unused'))
    plans=list(c.user_data.get('pending_ai_bookings',{}).values())
    return plans[-1] if plans else None


def confirm(u,c,plan,action='confirm',calendar=CALENDAR):
    u.callback_query.data=f'ai_{action}:{plan.token}'
    run(h.handle_ai_booking_callback(u,c,1,'unused',calendar))


def context():return SimpleNamespace(user_data={})


def test_preparation_never_writes(env):
    u=update();c=context();p=prepare(u,c)
    assert env.registrations==env.writes==0
    assert p.intent==INTENT and p.request_key=='ai:2:3'
    assert (p.user_id,p.chat_id,p.calendar_id,p.owner_ref,p.phase)==(1,2,CALENDAR,h.OWNER_REF,'confirm')
    assert 0<p.expires-h.time.monotonic()<=300
    with pytest.raises(FrozenInstanceError):p.intent.cliente='changed'
    assert 'Conferma' in str(u.effective_message.reply_text.call_args)


def test_valid_confirmation_commits_once(env):
    u=update();c=context();p=prepare(u,c);confirm(u,c,p)
    assert env.row==('CREATED','a'*32)
    assert env.registrations==env.writes==env.markers==1
    assert env.force==[False]
    assert env.calls==['register','process','created']
    assert not c.user_data['pending_ai_bookings']


def test_cancel_only_draft(env):
    u=update();c=context();p=prepare(u,c);confirm(u,c,p,'cancel')
    assert not env.writes and not env.registrations
    assert 'Nessun appuntamento esistente cancellato' in u.callback_query.edit_message_text.call_args.args[0]


@pytest.mark.parametrize('condition',['expired','user','chat','calendar','owner','token','restart','legacy','wrong_phase','primary'])
def test_invalid_callback_no_write(env,condition):
    u=update();c=context();p=prepare(u,c);stored=p;calendar=CALENDAR;action='confirm'
    if condition=='expired':stored=replace(p,expires=0)
    if condition=='user':u.effective_user.id=7
    if condition=='chat':u.effective_chat.id=7
    if condition=='calendar':calendar='changed'
    if condition=='owner':stored=replace(p,owner_ref='other')
    if condition=='token':stored=replace(p,token='other')
    if condition=='legacy':stored={'intent':INTENT}
    if condition=='wrong_phase':action='overlap'
    if condition=='primary':calendar='primary'
    c.user_data['pending_ai_bookings'][p.token]=stored
    if condition=='restart':c.user_data={}
    confirm(u,c,p,action,calendar)
    assert env.writes==env.registrations==0


def test_duplicate_callback_no_replay(env):
    u=update();c=context();p=prepare(u,c);confirm(u,c,p);confirm(u,c,p)
    assert env.writes==env.registrations==1


def test_replaced_plan_invalidates_old_token(env):
    u=update();c=context();p=prepare(u,c)
    newer=prepare(u,c,replace(INTENT,ora='16:00'),key='ai:2:4')
    assert p.token not in c.user_data['pending_ai_bookings']
    confirm(u,c,p);assert not env.writes
    confirm(u,c,newer);assert env.writes==1


@pytest.mark.parametrize('initial_conflict',[True,False])
def test_initial_or_new_conflict_requires_second_confirmation(env,initial_conflict):
    if initial_conflict:env.availability=h.AvailabilityStatus.CONFLICT
    u=update();c=context();p=prepare(u,c)
    env.availability=h.AvailabilityStatus.CONFLICT
    confirm(u,c,p)
    assert not env.writes and not env.registrations
    second=list(c.user_data['pending_ai_bookings'].values())[0]
    assert second.phase=='overlap' and second.token!=p.token and second.request_key==p.request_key
    confirm(u,c,p);assert not env.writes
    confirm(u,c,second,'overlap')
    assert env.writes==1 and env.force==[True]


@pytest.mark.parametrize('phase',['prepare','confirm','overlap'])
def test_unknown_never_writes(env,phase):
    u=update();c=context()
    if phase=='prepare':env.availability=h.AvailabilityStatus.UNKNOWN
    p=prepare(u,c)
    if phase=='prepare':assert p is None
    else:
        if phase=='overlap':
            env.availability=h.AvailabilityStatus.CONFLICT
            confirm(u,c,p)
            p=list(c.user_data['pending_ai_bookings'].values())[0]
        env.availability=h.AvailabilityStatus.UNKNOWN
        confirm(u,c,p,'overlap' if phase=='overlap' else 'confirm')
    assert not env.writes and not env.registrations


@pytest.mark.parametrize('status',['CREATED','PENDING','UNKNOWN','FAILED'])
@pytest.mark.parametrize('when',['prepare','confirm'])
def test_persisted_request_no_new_write(env,status,when):
    u=update();c=context()
    if when=='prepare':env.row=(status,'a'*32)
    p=prepare(u,c)
    if when=='confirm':
        env.row=(status,'a'*32)
        confirm(u,c,p)
    assert not env.writes and not env.registrations
    text=(u.effective_message.reply_text if when=='prepare' else u.callback_query.edit_message_text).call_args.args[0]
    assert status in text


def test_result_delivery_failure_preserves_created(env):
    u=update();c=context();p=prepare(u,c)
    u.callback_query.edit_message_text.side_effect=RuntimeError('delivery failed')
    confirm(u,c,p)
    assert env.row==('CREATED','a'*32) and env.writes==1
    assert u.callback_query.edit_message_text.call_count==1
    # Re-delivery of the same original request shows the stored outcome only.
    prepare(u,c)
    assert env.writes==1
    assert 'CREATED' in u.effective_message.reply_text.call_args.args[0]


def test_preparation_delivery_failure_invalidates_token(env):
    u=update();c=context();u.effective_message.reply_text.side_effect=RuntimeError()
    assert prepare(u,c) is None
    assert not env.writes and not env.registrations


def test_conflict_prompt_delivery_failure_invalidates_token(env):
    u=update();c=context();p=prepare(u,c);env.availability=h.AvailabilityStatus.CONFLICT
    u.callback_query.edit_message_text.side_effect=RuntimeError()
    confirm(u,c,p)
    assert not c.user_data['pending_ai_bookings'] and not env.registrations


def test_text_cancel_does_not_delete_event(env):
    u=update();c=context();p=prepare(u,c);u.effective_message.text='Annulla prenotazione'
    assert run(h.handle_pending_creation_reply(u,c))
    confirm(u,c,p)
    assert not env.writes


def test_new_text_invalidates_plan_for_reinterpretation(env):
    u=update();c=context();p=prepare(u,c)
    u.effective_message.text='Cancella l’appuntamento esistente'
    assert not run(h.handle_pending_creation_reply(u,c))
    assert p.token not in c.user_data['pending_ai_bookings']


def test_same_user_conversations_isolated_by_chat(env,monkeypatch):
    c=context();first=update(chat=2);second=update(chat=8)
    interpreter=Mock();interpreter.interpret.return_value=AppointmentIntent('crea')
    monkeypatch.setattr(h,'interpreter',interpreter)
    run(h.handle_ai_message(first,c,1,CALENDAR,'unused'))
    run(h.handle_ai_message(second,c,1,CALENDAR,'unused'))
    assert set(h.conversations)=={(1,2),(1,8)}
    assert interpreter.interpret.call_count==2
    assert not env.writes


def test_other_chat_does_not_invalidate_plan(env):
    u=update();c=context();p=prepare(u,c)
    assert not run(h.handle_pending_creation_reply(update(chat=8),c))
    assert p.token in c.user_data['pending_ai_bookings']


def test_db_error_before_registration_no_write(env,monkeypatch):
    u=update();c=context();p=prepare(u,c)
    monkeypatch.setattr(h,'get_booking_operation',Mock(side_effect=RuntimeError()))
    confirm(u,c,p)
    assert not env.writes and not env.registrations


def test_final_commit_error_does_not_retry_write(env,monkeypatch):
    u=update();c=context();p=prepare(u,c)
    monkeypatch.setattr(h,'mark_booking_created',Mock(side_effect=RuntimeError()))
    confirm(u,c,p)
    assert env.writes==1
    assert 'Non ripetere automaticamente' in u.callback_query.edit_message_text.call_args.args[0]


def test_service_conflict_no_force_retry(env):
    u=update();c=context();p=prepare(u,c);env.result=BookingStatus.CONFLICT
    confirm(u,c,p)
    assert env.row[0]=='FAILED' and env.force==[False]
    assert env.writes==1  # One simulated service invocation, never a forced retry.


def test_service_unknown_stays_unknown(env):
    u=update();c=context();p=prepare(u,c);env.result=BookingStatus.UNKNOWN
    confirm(u,c,p)
    assert env.row[0]=='UNKNOWN' and env.writes==1


def test_cancel_incomplete_creation_does_not_become_event_deletion(env):
    u=update(text='Annulla prenotazione');c=context()
    manager=h.ConversationManager();manager.start(AppointmentIntent('crea'),'original-key')
    h.conversations[(1,2)]=manager
    assert run(h.handle_pending_creation_reply(u,c))
    assert not h.conversations and not env.writes


def test_latest_prewrite_unknown_blocks_even_after_overlap(env,monkeypatch):
    u=update();c=context();p=prepare(u,c)
    env.availability=h.AvailabilityStatus.CONFLICT;confirm(u,c,p)
    p=list(c.user_data['pending_ai_bookings'].values())[0]
    monkeypatch.setattr(h,'check_ai_availability',Mock(side_effect=[h.AvailabilityStatus.CONFLICT,h.AvailabilityStatus.UNKNOWN]))
    confirm(u,c,p,'overlap')
    assert not env.registrations and not env.writes


def test_service_detects_late_conflict_without_an_insert(env,monkeypatch):
    u=update();c=context();p=prepare(u,c)
    def process(*args,**kwargs):
        assert kwargs['force_booking'] is False
        env.calls.append('service-conflict')
        return BookingResult(BookingStatus.CONFLICT)
    monkeypatch.setattr(h,'process_booking',process)
    confirm(u,c,p)
    assert env.row[0]=='FAILED' and env.registrations==1
    assert env.writes==env.markers==0
    assert not c.user_data['pending_ai_bookings']


def test_registration_race_never_creates_again(env,monkeypatch):
    u=update();c=context();p=prepare(u,c)
    monkeypatch.setattr(h,'register_ai_booking',Mock(return_value=h.RegistrationResult.ALREADY_EXISTS))
    confirm(u,c,p)
    assert not env.writes


@pytest.mark.parametrize('text',['','ai_confirm','ai_confirm:missing','ai_confirm:x:extra'])
def test_malformed_callback_no_side_effect(env,text):
    u=update();c=context();prepare(u,c);u.callback_query.data=text
    run(h.handle_ai_booking_callback(u,c,1,'unused',CALENDAR))
    assert not env.writes and not env.registrations


def test_conflict_at_last_pre_registration_check_requests_overlap(env,monkeypatch):
    u=update();c=context();p=prepare(u,c)
    monkeypatch.setattr(h,'check_ai_availability',Mock(side_effect=[h.AvailabilityStatus.AVAILABLE,h.AvailabilityStatus.CONFLICT]))
    confirm(u,c,p)
    assert not env.writes and not env.registrations
    overlap=list(c.user_data['pending_ai_bookings'].values())[0]
    assert overlap.phase=='overlap' and overlap.token!=p.token


def test_handler_import_without_project_root_on_sys_path():
    # Mirror the script's import layout without importing/running the bot.
    import subprocess
    directory=str(Path(__file__).resolve().parents[1]/'telegram-bot')
    code=f"import sys; sys.path.insert(0, {directory!r}); import ai_handler"
    subprocess.run([sys.executable,'-B','-c',code],cwd='/tmp',check=True,timeout=5,capture_output=True)
