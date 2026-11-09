"""Offline separation of operational outcomes and Telegram delivery."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import threading
import pytest
from test_ai_reschedule import make_update
import test_ai_reschedule as move
import test_ai_deletion as delete
from telegram_delivery import deliver, deliver_session
from test_ai_creation_confirmation import env


@pytest.fixture(params=[move, delete], ids=['move', 'delete'])
def flow(request, monkeypatch):
    case=request.param; handler=case.handler
    key='ai_move' if case is move else 'ai_delete'
    prefix='aimove' if case is move else 'aidelete'
    monkeypatch.setattr(handler,'LOCK',asyncio.Lock())
    monkeypatch.setattr(handler,'google_service',Mock(return_value=object()))
    return case,handler,key,prefix


def setup(flow,stage='confirm'):
    case,h,key,prefix=flow
    u=make_update();s=case.session(stage)
    c=SimpleNamespace(user_data={key:s,'unrelated':object()})
    u.callback_query.data=f"{prefix}:{stage}:{s['token']}:0"
    return u,c,s


@pytest.mark.parametrize('outcome',['success','UNKNOWN','commit_error'])
def test_final_delivery_does_not_reclassify_or_retry(flow,monkeypatch,outcome):
    case,h,key,prefix=flow;u,c,s=setup(flow)
    persisted=[]
    result=('UPDATED' if case is move else 'DELETED') if outcome=='success' else 'UNKNOWN'
    def execute(*args):
        if outcome=='commit_error':raise RuntimeError('commit not confirmed')
        persisted.append(result)
        return result
    worker=Mock(side_effect=execute);monkeypatch.setattr(h,'execute',worker)
    u.callback_query.edit_message_text.side_effect=RuntimeError('private-secret')
    async def run():
        await h.callback(u,c,1,'test-calendar','unused')
        assert key not in c.user_data
        assert u.callback_query.edit_message_text.await_count==1
        text=u.callback_query.edit_message_text.call_args.args[0]
        assert ('✅' in text)==(outcome=='success')
        if outcome == 'UNKNOWN':
            assert 'incerto' in text and 'non ripetere automaticamente' in text
        await h.callback(u,c,1,'test-calendar','unused')
    asyncio.run(run())
    worker.assert_called_once()
    assert persisted==([] if outcome=='commit_error' else [result])


def test_answer_failure_still_executes_once(flow,monkeypatch):
    case,h,key,prefix=flow;u,c,s=setup(flow)
    worker=Mock(return_value='UPDATED' if case is move else 'DELETED')
    monkeypatch.setattr(h,'execute',worker)
    u.callback_query.answer.side_effect=RuntimeError('expired acknowledgement')
    async def run():
        await h.callback(u,c,1,'test-calendar','unused')
        await h.callback(u,c,1,'test-calendar','unused')
    asyncio.run(run());worker.assert_called_once()
    assert key not in c.user_data


@pytest.mark.parametrize('count',[1,2])
def test_search_prompt_failure_invalidates_only_session(flow,monkeypatch,count):
    case,h,key,prefix=flow;u=make_update();sentinel=object()
    c=SimpleNamespace(user_data={'unrelated':sentinel})
    monkeypatch.setattr(h,'candidates',Mock(return_value=[case.EVENT]*count))
    plan=(case.PLAN,move.svc.AvailabilityStatus.AVAILABLE) if case is move else case.PLAN
    monkeypatch.setattr(h,'prepare',Mock(return_value=plan))
    worker=Mock();monkeypatch.setattr(h,'execute',worker)
    u.effective_message.reply_text.side_effect=RuntimeError('lost response')
    asyncio.run(h.begin(u,c,case.INTENT,'key','test-calendar','unused'))
    assert key not in c.user_data and c.user_data['unrelated'] is sentinel
    assert u.effective_message.reply_text.await_count==1
    worker.assert_not_called()


def test_selection_edit_failure_never_restores_consumed_token(flow,monkeypatch):
    case,h,key,prefix=flow;u,c,s=setup(flow,'select');s['ids']=[case.EVENT['id']]
    plan=(case.PLAN,move.svc.AvailabilityStatus.AVAILABLE) if case is move else case.PLAN
    monkeypatch.setattr(h,'prepare',Mock(return_value=plan))
    worker=Mock();monkeypatch.setattr(h,'execute',worker)
    u.callback_query.edit_message_text.side_effect=RuntimeError('lost edit')
    asyncio.run(h.callback(u,c,1,'test-calendar','unused'))
    assert key not in c.user_data
    assert u.callback_query.edit_message_text.await_count==1
    worker.assert_not_called()


def test_conflict_delivery_failure_invalidates_new_token(monkeypatch):
    flow=(move,move.handler,'ai_move','aimove');u,c,s=setup(flow)
    monkeypatch.setattr(move.handler,'LOCK',asyncio.Lock())
    monkeypatch.setattr(move.handler,'google_service',Mock(return_value=object()))
    worker=Mock(return_value='CONFLICT');monkeypatch.setattr(move.handler,'execute',worker)
    u.callback_query.edit_message_text.side_effect=RuntimeError('lost conflict')
    async def run():
        await move.handler.callback(u,c,1,'test-calendar','unused')
        assert 'ai_move' not in c.user_data
        await move.handler.callback(u,c,1,'test-calendar','unused')
    asyncio.run(run());worker.assert_called_once()
    assert worker.call_args.args[-1] is False


def test_missing_question_failure_and_cancel(flow,monkeypatch):
    case,h,key,prefix=flow;u=make_update();c=SimpleNamespace(user_data={'unrelated':1})
    u.effective_message.reply_text.side_effect=RuntimeError('question lost')
    asyncio.run(h.begin(u,c,replace(case.INTENT,cliente=None),'key','test-calendar','unused'))
    assert c.user_data=={'unrelated':1}
    u,c,s=setup(flow);u.callback_query.data=f"{prefix}:cancel:{s['token']}:0"
    u.callback_query.answer.side_effect=RuntimeError('answer lost')
    u.callback_query.edit_message_text.side_effect=RuntimeError('cancel lost')
    worker=Mock();monkeypatch.setattr(h,'execute',worker)
    asyncio.run(h.callback(u,c,1,'test-calendar','unused'))
    assert key not in c.user_data;worker.assert_not_called()


def test_helper_sanitizes_and_does_not_swallow_cancellation(capsys):
    send=AsyncMock(side_effect=RuntimeError('private-secret'))
    assert asyncio.run(deliver(send,'hello')) is False
    send.assert_awaited_once();assert 'private-secret' not in capsys.readouterr().out
    send=AsyncMock(side_effect=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):asyncio.run(deliver(send))


def test_failed_delivery_does_not_remove_replacement_session():
    original,other=object(),object();c=SimpleNamespace(user_data={'plan':other})
    send=AsyncMock(side_effect=RuntimeError())
    assert not asyncio.run(deliver_session(c,'plan',original,send,'hello'))
    assert c.user_data['plan'] is other


def test_creation_missing_question_invalidates_only_its_conversation(monkeypatch):
    import ai_handler as h
    from test_ai_creation_confirmation import update,INTENT
    u=update();u.effective_message.reply_text.side_effect=RuntimeError('question lost')
    other=object();monkeypatch.setattr(h,'conversations',{(99,99):other})
    monkeypatch.setattr(h,'conversation_locks',asyncio.Lock())
    monkeypatch.setattr(h.interpreter,'interpret',Mock(return_value=replace(INTENT,cliente=None)))
    asyncio.run(h.handle_ai_message(u,SimpleNamespace(user_data={}),1,'test-calendar','unused'))
    assert h.conversations=={(99,99):other}
    u.effective_message.reply_text.assert_awaited_once()


def test_cancelled_callback_waits_for_worker(flow,monkeypatch):
    case,h,key,prefix=flow;u,c,s=setup(flow)
    started,release,finished=threading.Event(),threading.Event(),threading.Event()
    calls=[]
    def worker(*args):
        started.set()
        try:
            assert release.wait(2)
            calls.append('write')
            return 'UPDATED' if case is move else 'DELETED'
        finally:finished.set()
    monkeypatch.setattr(h,'execute',worker)
    async def run():
        task=asyncio.create_task(h.callback(u,c,1,'test-calendar','unused'))
        try:
            for _ in range(500):
                if started.is_set():break
                await asyncio.sleep(.001)
            assert started.is_set();task.cancel();await asyncio.sleep(.01)
            assert not task.done() and h.LOCK.locked()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):await task
        assert finished.is_set() and not h.LOCK.locked()
    asyncio.run(run());assert calls==['write'] and key not in c.user_data
    u.callback_query.edit_message_text.assert_not_awaited()


@pytest.mark.parametrize('status',['CREATED','UNKNOWN'])
def test_creation_delivery_preserves_persisted_state(env,status):
    import ai_handler as h
    from test_ai_creation_confirmation import update,context,prepare,confirm
    u=update();c=context();plan=prepare(u,c)
    env.result=getattr(h.BookingStatus,status)
    u.callback_query.answer.side_effect=RuntimeError('ack lost')
    u.callback_query.edit_message_text.side_effect=RuntimeError('result lost')
    confirm(u,c,plan)
    assert env.row[0]==status and env.writes==1 and env.registrations==1
    assert u.callback_query.edit_message_text.await_count==1
    confirm(u,c,plan)
    assert env.writes==1


@pytest.mark.parametrize('mode',['expired','unauthorized'])
def test_invalid_callback_with_answer_failure_never_executes(flow,monkeypatch,mode):
    case,h,key,prefix=flow;u,c,s=setup(flow)
    if mode=='expired':s['expires']=0
    else:u.effective_user.id=9
    u.callback_query.answer.side_effect=RuntimeError('ack lost')
    worker=Mock();monkeypatch.setattr(h,'execute',worker)
    asyncio.run(h.callback(u,c,1,'test-calendar','unused'))
    worker.assert_not_called();h.google_service.assert_not_called()
