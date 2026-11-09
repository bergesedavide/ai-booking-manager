import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock, patch
import pytest
from googleapiclient.errors import HttpError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'telegram-bot'))
import ai_reschedule_service as svc
import ai_reschedule_handler as handler
from ai.schemas import AppointmentIntent

NOW=datetime(2026,11,20,14,tzinfo=timezone.utc)
NEW=datetime(2026,11,21,14,tzinfo=timezone.utc)
EVENT={'id':'a'*32,'status':'confirmed','etag':'v1','summary':'Allenamento - Test',
       'start':{'dateTime':NOW.isoformat()},'end':{'dateTime':NOW.replace(minute=30).isoformat()}}
INTENT=AppointmentIntent('modifica','Test','2026-11-21','15:00',None,'2026-11-20',None)
PLAN=svc.MovePlan('ai:1:2','a'*32,'test-calendar','owner','v1',EVENT['summary'],NOW,NOW.replace(minute=30),NEW,NEW.replace(minute=30))

class FakeGoogle:
    """Supports reads and one simulated PATCH, forbids all other methods."""
    def __init__(self, event=EVENT, patch_error=None):
        self.event=event; self.patch_error=patch_error; self.patches=[]; self.headers={}
        self.mode=None
    def events(self): return self
    def get(self,**kw): self.mode='get'; return self
    def patch(self,**kw):
        self.patches.append(kw); self.mode='patch'; return self
    def execute(self,**kw):
        if self.mode=='get': return self.event
        assert self.headers['If-Match']=='v1'
        if self.patch_error: raise self.patch_error
        return {**self.event, 'etag':'v2',**self.patches[-1]['body']}
    def __getattr__(self,name): raise AssertionError('Forbidden Google method: '+name)


def repository():
    repo=MagicMock()
    repo.owned.return_value=12
    repo.register.return_value=1
    return repo


def test_real_deterministic_updater_simulated():
    google=FakeGoogle();repo=repository()
    with patch.object(svc,'availability',return_value=svc.AvailabilityStatus.AVAILABLE):
        assert svc.execute(google,repo,PLAN,'test-calendar','owner')=='UPDATED'
    assert len(google.patches)==1
    assert set(google.patches[0]['body'])=={'start','end'}
    repo.finish.assert_called_once_with(1,'UPDATED','UPDATED')

@pytest.mark.parametrize('changes', [{'owner_ref':'other'},{'calendar_id':'other'}])
def test_wrong_context_no_write(changes):
    google=FakeGoogle()
    assert svc.execute(google,repository(),replace(PLAN,**changes),'test-calendar','owner')=='REJECTED'
    assert not google.patches


def test_external_event_never_written():
    google=FakeGoogle();repo=repository();repo.owned.return_value=None
    assert svc.execute(google,repo,PLAN,'test-calendar','owner')=='REJECTED'
    repo.register.assert_not_called();assert not google.patches

@pytest.mark.parametrize('status,allowed,expected',[
    (svc.AvailabilityStatus.CONFLICT,False,'CONFLICT'),
    (svc.AvailabilityStatus.UNKNOWN,False,'UNAVAILABLE'),
    (svc.AvailabilityStatus.UNKNOWN,True,'UNAVAILABLE'),
    (None,True,'UNAVAILABLE'),
])
def test_blocked_availability(status,allowed,expected):
    repo=repository();google=FakeGoogle()
    with patch.object(svc,'availability',return_value=status):
        assert svc.execute(google,repo,PLAN,'test-calendar','owner',allowed)==expected
    repo.register.assert_not_called();assert not google.patches


def test_explicit_overlap():
    google=FakeGoogle()
    with patch.object(svc,'availability',return_value=svc.AvailabilityStatus.CONFLICT):
        assert svc.execute(google,repository(),PLAN,'test-calendar','owner',True)=='UPDATED'
    assert len(google.patches)==1


def test_stale_etag():
    google=FakeGoogle({**EVENT,'etag':'external'})
    repo=repository()
    assert svc.execute(google,repo,PLAN,'test-calendar','owner')=='STALE'
    repo.register.assert_not_called();assert not google.patches

@pytest.mark.parametrize('stage',['register','start'])
def test_db_failure_prevents_patch(stage):
    google=FakeGoogle();repo=repository();getattr(repo,stage).side_effect=RuntimeError()
    with patch.object(svc,'availability',return_value=svc.AvailabilityStatus.AVAILABLE),pytest.raises(RuntimeError):
        svc.execute(google,repo,PLAN,'test-calendar','owner')
    assert not google.patches


def test_duplicate_or_unknown_blocks():
    repo=repository();repo.register.return_value=None;google=FakeGoogle()
    with patch.object(svc,'availability',return_value=svc.AvailabilityStatus.AVAILABLE):
        assert svc.execute(google,repo,PLAN,'test-calendar','owner')=='DUPLICATE_OR_UNRESOLVED'
    assert not google.patches

@pytest.mark.parametrize('error,status,outcome',[
    (TimeoutError(),'UNKNOWN','UNKNOWN'),
    (HttpError(SimpleNamespace(status=412,reason='changed'),b'{}'),'FAILED','STALE'),
    (HttpError(SimpleNamespace(status=503,reason='error'),b'{}'),'UNKNOWN','UNKNOWN'),
])
def test_no_retry_on_patch_error(error,status,outcome):
    google=FakeGoogle(patch_error=error);repo=repository()
    with patch.object(svc,'availability',return_value=svc.AvailabilityStatus.AVAILABLE):
        assert svc.execute(google,repo,PLAN,'test-calendar','owner')==outcome
    assert len(google.patches)==1
    repo.finish.assert_called_once_with(1,status,outcome)


def test_final_commit_failure_no_retry():
    google=FakeGoogle();repo=repository();repo.finish.side_effect=RuntimeError()
    with patch.object(svc,'availability',return_value=svc.AvailabilityStatus.AVAILABLE),pytest.raises(RuntimeError):
        svc.execute(google,repo,PLAN,'test-calendar','owner')
    assert len(google.patches)==1


def test_candidates_require_db_provenance_not_title():
    repo=repository();repo.owned.side_effect=lambda eid: 12 if eid=='a'*32 else None
    external={**EVENT,'id':'external'}
    with patch.object(svc,'list_events_in_interval',return_value=[external,EVENT]):
        assert svc.candidates(FakeGoogle(),repo,INTENT,'test-calendar','owner')==[EVENT]


def test_multiple_candidates_preserved():
    other={**EVENT,'id':'b'*32}
    with patch.object(svc,'list_events_in_interval',return_value=[EVENT,other]):
        assert len(svc.candidates(FakeGoogle(),repository(),INTENT,'test-calendar','owner'))==2


def test_prepare_maintains_duration():
    with patch.object(svc,'availability',return_value=svc.AvailabilityStatus.AVAILABLE):
        plan,_=svc.prepare(FakeGoogle(),repository(),INTENT,EVENT['id'],'ai:1:2','test-calendar','owner')
    assert plan.new_end-plan.new_start==plan.old_end-plan.old_start
    assert plan.title==EVENT['summary']

@pytest.mark.parametrize('intent',[replace(INTENT,durata_minuti=60),replace(INTENT,data='2026-10-25',ora='02:30'),replace(INTENT,data='2026-03-29',ora='02:30')])
def test_unsupported_duration_and_dst(intent):
    with pytest.raises(ValueError):
        svc.prepare(FakeGoogle(),repository(),intent,EVENT['id'],'key','test-calendar','owner')


def make_update():
    update=MagicMock();update.effective_user.id=1;update.effective_chat.id=2
    update.effective_message.reply_text=AsyncMock()
    update.callback_query.answer=AsyncMock();update.callback_query.edit_message_text=AsyncMock()
    return update


def session(stage='confirm'):
    s=dict(user_id=1,chat_id=2,calendar_id='test-calendar',owner_ref=handler.OWNER_REF,stage=stage,plan=replace(PLAN,owner_ref=handler.OWNER_REF),intent=INTENT,request_key='key')
    handler.renew(s);return s


def test_callback_single_use():
    update=make_update();s=session();context=SimpleNamespace(user_data={'ai_move':s})
    update.callback_query.data=f"aimove:confirm:{s['token']}:0"
    async def run():
        with patch.object(handler,'google_service',return_value=FakeGoogle()),patch.object(handler,'execute',return_value='UPDATED') as execute:
            await handler.callback(update,context,1,'test-calendar','password')
            await handler.callback(update,context,1,'test-calendar','password')
            assert execute.call_count==1
    asyncio.run(run())

@pytest.mark.parametrize('change',['expired','user','chat','calendar'])
def test_invalid_callback_no_google(change):
    update=make_update();s=session();context=SimpleNamespace(user_data={'ai_move':s})
    update.callback_query.data=f"aimove:confirm:{s['token']}:0"
    if change=='expired':s['expires']=0
    if change=='user':update.effective_user.id=7
    if change=='chat':update.effective_chat.id=7
    if change=='calendar':s['calendar_id']='other'
    with patch.object(handler,'google_service') as factory:
        asyncio.run(handler.callback(update,context,1,'test-calendar','password'))
    factory.assert_not_called()


def test_new_conflict_requires_second_explicit_action():
    update=make_update();s=session();context=SimpleNamespace(user_data={'ai_move':s})
    update.callback_query.data=f"aimove:confirm:{s['token']}:0"
    async def run():
        with patch.object(handler,'google_service',return_value=FakeGoogle()),patch.object(handler,'execute',side_effect=['CONFLICT','UPDATED']) as execute:
            await handler.callback(update,context,1,'test-calendar','password')
            assert execute.call_args.args[-1] is False
            pending=context.user_data['ai_move'];assert pending['stage']=='overlap'
            update.callback_query.data=f"aimove:overlap:{pending['token']}:0"
            await handler.callback(update,context,1,'test-calendar','password')
            assert execute.call_args.args[-1] is True
    asyncio.run(run())


def test_collect_missing_fields_without_google():
    update=make_update();context=SimpleNamespace(user_data={})
    with patch.object(handler,'google_service') as factory:
        asyncio.run(handler.begin(update,context,replace(INTENT,cliente=None),'key','test-calendar','password'))
    factory.assert_not_called()
    assert context.user_data['ai_move']['field']=='cliente'


def test_select_does_not_write():
    update=make_update();s=session('select');s['ids']=[EVENT['id'],'b'*32]
    context=SimpleNamespace(user_data={'ai_move':s});update.callback_query.data=f"aimove:select:{s['token']}:1"
    with patch.object(handler,'google_service',return_value=FakeGoogle()),patch.object(handler,'prepare',return_value=(PLAN,svc.AvailabilityStatus.AVAILABLE)) as prepare,patch.object(handler,'execute') as execute:
        asyncio.run(handler.callback(update,context,1,'test-calendar','password'))
    assert prepare.call_args.args[3]=='b'*32
    execute.assert_not_called()
    assert context.user_data['ai_move']['stage']=='confirm'


def test_unknown_response_keeps_uncertainty():
    repo=repository();google=FakeGoogle()
    with patch.object(svc,'availability',return_value=svc.AvailabilityStatus.AVAILABLE),patch.object(svc,'update_booking',return_value={'id':PLAN.event_id}) as update:
        assert svc.execute(google,repo,PLAN,'test-calendar','owner')=='UNKNOWN'
    update.assert_called_once()
    repo.finish.assert_called_once_with(1,'UNKNOWN','UNKNOWN')


def test_decode_error_after_patch_is_unknown():
    google=FakeGoogle(patch_error=ValueError('invalid response'));repo=repository()
    with patch.object(svc,'availability',return_value=svc.AvailabilityStatus.AVAILABLE):
        assert svc.execute(google,repo,PLAN,'test-calendar','owner')=='UNKNOWN'
    assert len(google.patches)==1
    repo.finish.assert_called_once_with(1,'UNKNOWN','UNKNOWN')


def test_cancel_callback_no_google():
    update=make_update();s=session();context=SimpleNamespace(user_data={'ai_move':s})
    update.callback_query.data=f"aimove:cancel:{s['token']}:0"
    with patch.object(handler,'google_service') as factory:
        asyncio.run(handler.callback(update,context,1,'test-calendar','password'))
    factory.assert_not_called();assert not context.user_data


def test_no_candidates_no_plan():
    update=make_update();context=SimpleNamespace(user_data={})
    with patch.object(handler,'google_service',return_value=FakeGoogle()),patch.object(handler,'candidates',return_value=[]),patch.object(handler,'prepare') as prepare:
        asyncio.run(handler.begin(update,context,INTENT,'key','test-calendar','password'))
    prepare.assert_not_called();assert not context.user_data


def test_multiple_candidates_only_prompts_selection():
    update=make_update();context=SimpleNamespace(user_data={})
    with patch.object(handler,'google_service',return_value=FakeGoogle()),patch.object(handler,'candidates',return_value=[EVENT,{**EVENT,'id':'b'*32}]),patch.object(handler,'prepare') as prepare:
        asyncio.run(handler.begin(update,context,INTENT,'key','test-calendar','password'))
    prepare.assert_not_called()
    assert context.user_data['ai_move']['stage']=='select'


def test_reply_completes_original_day_before_search():
    update=make_update();context=SimpleNamespace(user_data={})
    async def run():
        with patch.object(handler,'google_service',return_value=FakeGoogle()),patch.object(handler,'candidates',return_value=[]) as search:
            await handler.begin(update,context,replace(INTENT,data_originale=None),'key','test-calendar','password')
            search.assert_not_called()
            update.effective_message.text='2026-11-20'
            assert await handler.handle_reply(update,context,'test-calendar','password')
            assert search.call_args.args[2].data_originale=='2026-11-20'
    asyncio.run(run())


def test_dispatch_modification_only():
    import ai_handler
    update=make_update();context=SimpleNamespace(user_data={})
    with patch.object(handler,'begin',new_callable=AsyncMock) as begin,patch.object(ai_handler,'get_connection') as connection:
        asyncio.run(ai_handler.send_ai_result(update,context,INTENT,'test-calendar','key','password'))
    begin.assert_awaited_once();connection.assert_not_called()


def test_creation_dispatch_still_uses_existing_path():
    import ai_handler
    update=make_update();context=SimpleNamespace(user_data={})
    with patch.object(handler,'begin',new_callable=AsyncMock) as begin,patch.object(ai_handler,'get_connection'),patch.object(ai_handler,'get_booking_operation',return_value=('CREATED','event')),patch.object(ai_handler,'format_ai_result',new_callable=AsyncMock,return_value='existing result') as old:
        asyncio.run(ai_handler.send_ai_result(update,context,AppointmentIntent('crea'),'test-calendar','key','password'))
    begin.assert_not_awaited();old.assert_awaited_once()
