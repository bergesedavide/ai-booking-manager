import asyncio
from dataclasses import replace
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock, patch
import pytest
from googleapiclient.errors import HttpError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'telegram-bot'))
from test_ai_reschedule import EVENT, NOW, INTENT as MOVE_INTENT, make_update
from ai.schemas import AppointmentIntent
import ai_deletion_service as svc
import ai_deletion_handler as handler

INTENT=AppointmentIntent('cancella','Test','2026-11-20','15:00')
PLAN=svc.DeletePlan('ai:1:2',EVENT['id'],'test-calendar','owner','v1',EVENT['summary'],NOW,NOW.replace(minute=30))

class FakeGoogle:
    """No insert/patch methods; exactly one simulated DELETE permitted."""
    def __init__(self,event=EVENT,error=None,read_error=None):
        self.event=event;self.error=error;self.read_error=read_error
        self.deletes=[];self.headers={};self.reads=0
    def events(self):return self
    def get(self,**kwargs):self.mode='get';self.reads+=1;return self
    def list(self,**kwargs):self.mode='list';return self
    def delete(self,**kwargs):
        assert not self.deletes,'Delete retried'
        self.deletes.append(kwargs);self.mode='delete';return self
    def execute(self,num_retries=0):
        assert num_retries==0
        if self.mode=='get':
            if self.read_error:raise self.read_error
            return self.event
        if self.mode=='list':return {'items':[self.event]}
        assert self.headers['If-Match']=='v1'
        if self.error:raise self.error
        return None
    def __getattr__(self,name):raise AssertionError('Forbidden Google method '+name)


def repo():
    r=MagicMock();r.owned.return_value=12;r.register.return_value=1
    return r


def test_delete_once_with_if_match():
    g=FakeGoogle();r=repo()
    assert svc.execute(g,r,PLAN,'test-calendar','owner')=='DELETED'
    assert g.deletes==[{'calendarId':'test-calendar','eventId':PLAN.event_id}]
    r.finish.assert_called_once_with(1,'DELETED','DELETED')
    assert g.reads==2  # precheck plus deterministic delete precheck, no GET after.

@pytest.mark.parametrize('status',[404,410,503])
def test_uncertain_http_never_success(status):
    g=FakeGoogle(error=HttpError(SimpleNamespace(status=status,reason='error'),b'{}'));r=repo()
    assert svc.execute(g,r,PLAN,'test-calendar','owner')=='UNKNOWN'
    assert len(g.deletes)==1
    r.finish.assert_called_once_with(1,'UNKNOWN','UNKNOWN')

@pytest.mark.parametrize('error',[TimeoutError(),ValueError('bad response')])
def test_uncertain_errors(error):
    g=FakeGoogle(error=error);r=repo()
    assert svc.execute(g,r,PLAN,'test-calendar','owner')=='UNKNOWN'
    assert len(g.deletes)==1


def test_412_failed_stale():
    g=FakeGoogle(error=HttpError(SimpleNamespace(status=412,reason='changed'),b'{}'));r=repo()
    assert svc.execute(g,r,PLAN,'test-calendar','owner')=='STALE'
    r.finish.assert_called_once_with(1,'FAILED','STALE')
    assert len(g.deletes)==1

@pytest.mark.parametrize('changes',[{'etag':None},{'etag':'v2'}])
def test_missing_or_changed_etag(changes):
    g=FakeGoogle({**EVENT,**changes});r=repo()
    assert svc.execute(g,r,PLAN,'test-calendar','owner')=='STALE'
    assert not g.deletes;r.register.assert_not_called()

@pytest.mark.parametrize('status',[404,410])
def test_initial_absence_not_success(status):
    g=FakeGoogle(read_error=HttpError(SimpleNamespace(status=status,reason='missing'),b'{}'));r=repo()
    with pytest.raises(HttpError):svc.execute(g,r,PLAN,'test-calendar','owner')
    assert not g.deletes;r.register.assert_not_called()

@pytest.mark.parametrize('stage',['owned','register','start'])
def test_db_errors_block_write(stage):
    g=FakeGoogle();r=repo();getattr(r,stage).side_effect=RuntimeError()
    with pytest.raises(RuntimeError):svc.execute(g,r,PLAN,'test-calendar','owner')
    assert not g.deletes


def test_commit_failure_never_returns_success():
    g=FakeGoogle();r=repo();r.finish.side_effect=RuntimeError()
    with pytest.raises(RuntimeError):svc.execute(g,r,PLAN,'test-calendar','owner')
    assert len(g.deletes)==1

@pytest.mark.parametrize('where',['owned','register'])
def test_unowned_unresolved_or_duplicate_blocks(where):
    g=FakeGoogle();r=repo();getattr(r,where).return_value=None
    assert svc.execute(g,r,PLAN,'test-calendar','owner')=='BLOCKED'
    assert not g.deletes

@pytest.mark.parametrize('changes',[{'owner_ref':'other'},{'calendar_id':'other'}])
def test_context_change(changes):
    g=FakeGoogle()
    assert svc.execute(g,repo(),replace(PLAN,**changes),'test-calendar','owner')=='BLOCKED'
    assert not g.deletes


def test_primary_never_contacts_google():
    g=FakeGoogle()
    with pytest.raises(ValueError):svc.execute(g,repo(),PLAN,'primary','owner')
    assert g.reads==0 and not g.deletes


def test_external_identical_title_excluded():
    g=FakeGoogle();r=repo();r.owned.return_value=None
    assert svc.candidates(g,r,INTENT,'test-calendar','owner')==[]
    assert not g.deletes


def test_prepare_snapshot():
    g=FakeGoogle()
    assert svc.prepare(g,repo(),INTENT,PLAN.event_id,PLAN.request_key,'test-calendar','owner')==PLAN
    assert not g.deletes


def session(stage='confirm'):
    s=dict(user_id=1,chat_id=2,calendar_id='test-calendar',owner_ref=handler.OWNER_REF,
        stage=stage,intent=INTENT,request_key='key',plan=replace(PLAN,owner_ref=handler.OWNER_REF))
    handler.renew(s);return s


def test_duplicate_callback_consumed_once():
    u=make_update();s=session();c=SimpleNamespace(user_data={'ai_delete':s})
    u.callback_query.data=f"aidelete:confirm:{s['token']}:0"
    async def run():
        with patch.object(handler,'google_service',return_value=FakeGoogle()),patch.object(handler,'execute',return_value='DELETED') as execute:
            await handler.callback(u,c,1,'test-calendar','password')
            await handler.callback(u,c,1,'test-calendar','password')
            assert execute.call_count==1
    asyncio.run(run())

@pytest.mark.parametrize('condition',['expired','user','chat','calendar','restart','bad_token'])
def test_invalid_confirmation_no_google(condition):
    u=make_update();s=session();c=SimpleNamespace(user_data={'ai_delete':s})
    u.callback_query.data=f"aidelete:confirm:{s['token']}:0"
    if condition=='expired':s['expires']=0
    if condition=='user':u.effective_user.id=7
    if condition=='chat':u.effective_chat.id=7
    if condition=='calendar':s['calendar_id']='other'
    if condition=='restart':c.user_data={}
    if condition=='bad_token':u.callback_query.data='aidelete:confirm:0000000000000000:0'
    with patch.object(handler,'google_service') as factory:
        asyncio.run(handler.callback(u,c,1,'test-calendar','password'))
    factory.assert_not_called()


def test_cancel_no_google():
    u=make_update();s=session();c=SimpleNamespace(user_data={'ai_delete':s})
    u.callback_query.data=f"aidelete:cancel:{s['token']}:0"
    with patch.object(handler,'google_service') as factory:
        asyncio.run(handler.callback(u,c,1,'test-calendar','password'))
    factory.assert_not_called();assert not c.user_data


def test_text_cancel_no_google():
    u=make_update();u.effective_message.text='annulla';c=SimpleNamespace(user_data={'ai_delete':session()})
    with patch.object(handler,'google_service') as factory:
        assert asyncio.run(handler.handle_reply(u,c,'test-calendar','password'))
    factory.assert_not_called();assert not c.user_data


def test_ambiguous_search_selection_does_not_delete():
    u=make_update();c=SimpleNamespace(user_data={})
    async def run():
        with patch.object(handler,'google_service',return_value=FakeGoogle()),patch.object(handler,'candidates',return_value=[EVENT,{**EVENT,'id':'b'*32}]),patch.object(handler,'prepare',return_value=PLAN) as prepare,patch.object(handler,'execute') as execute:
            await handler.begin(u,c,INTENT,'key','test-calendar','password')
            s=c.user_data['ai_delete'];assert s['stage']=='select';prepare.assert_not_called()
            u.callback_query.data=f"aidelete:select:{s['token']}:1"
            await handler.callback(u,c,1,'test-calendar','password')
            assert prepare.call_args.args[3]=='b'*32
            assert c.user_data['ai_delete']['stage']=='confirm'
            execute.assert_not_called()
    asyncio.run(run())


def test_collect_date_without_google():
    u=make_update();c=SimpleNamespace(user_data={})
    async def run():
        with patch.object(handler,'google_service',return_value=FakeGoogle()),patch.object(handler,'candidates',return_value=[]) as search:
            await handler.begin(u,c,replace(INTENT,data=None),'key','test-calendar','password')
            search.assert_not_called()
            u.effective_message.text='2026-11-20'
            await handler.handle_reply(u,c,'test-calendar','password')
            assert search.call_args.args[2].data=='2026-11-20'
    asyncio.run(run())


def test_dispatch_dedicated_deletion():
    import ai_handler
    u=make_update();c=SimpleNamespace(user_data={})
    with patch.object(handler,'begin',new_callable=AsyncMock) as begin,patch.object(ai_handler,'get_connection') as connection:
        asyncio.run(ai_handler.send_ai_result(u,c,INTENT,'test-calendar','key','password'))
    begin.assert_awaited_once();connection.assert_not_called()
