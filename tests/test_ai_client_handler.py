"""Offline Telegram clients: fake AI/storage, no services or bot polling."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from pathlib import Path
import sys
import threading

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'telegram-bot'))
import ai_client_handler as client
import ai_handler as ai
from ai.client_schemas import ClientIntent, ClientResponseError, UnsupportedClientIntent
from ai.ollama_client import OllamaError, OllamaErrorKind
from client_service import ClientStatus, ClientResult
from test_ai_creation_confirmation import update

INTENT = ClientIntent('add_client','Marco','Rossi')
TEXT = 'Aggiungi Marco Rossi come cliente'


@pytest.fixture
def env(monkeypatch):
    service = Mock()
    service.schema_available.return_value = True
    service.lookup_request.return_value = ClientResult(ClientStatus.NOT_FOUND)
    service.add_client.return_value = ClientResult(ClientStatus.CREATED,12)
    monkeypatch.setattr(client,'ClientService',Mock(return_value=service))
    monkeypatch.setattr(client.interpreter,'interpret',Mock(return_value=INTENT))
    monkeypatch.setattr(ai,'conversations',{})
    monkeypatch.setattr(ai,'conversation_locks',asyncio.Lock())
    appointments = Mock(side_effect=AssertionError('Appointment AI forbidden for client path'))
    monkeypatch.setattr(ai.interpreter,'interpret',appointments)
    monkeypatch.setattr(ai,'check_ai_availability',Mock(side_effect=AssertionError('Google forbidden')))
    monkeypatch.setattr(ai,'process_booking',Mock(side_effect=AssertionError('Google forbidden')))
    return service


def context():return SimpleNamespace(user_data={})


def run(u,c):
    asyncio.run(ai.handle_ai_message(u,c,1,'test-calendar','fake-password'))


def message(text=TEXT, message_id=3, chat=2, user=1):
    return update(chat=chat,user=user,message=message_id,text=text)


@pytest.mark.parametrize('text', [TEXT,'Inserisci un nuovo cliente di nome Mario Rossi',
                                  'Registra Luca Bianchi, telefono 3331234567'])
def test_explicit_routing_and_immediate_insert(env,text):
    u=message(text);c=context();run(u,c)
    env.add_client.assert_called_once()
    args=env.add_client.call_args.kwargs
    assert args['authorized_telegram_user_id']==1 and args['request_key']=='client:2:3'
    assert args['allow_homonym'] is False
    assert not c.user_data['ai_clients']
    assert 'ID: 12' in u.effective_message.reply_text.call_args.args[0]
    ai.interpreter.interpret.assert_not_called();ai.check_ai_availability.assert_not_called()


@pytest.mark.parametrize('text',['Prenota Marco Rossi domani alle 15','Sposta Marco Rossi',
                                  'Cancella appuntamento di Marco','Registra appuntamento, telefono 123'])
def test_appointment_router_not_captured(env,text):
    assert not client.is_client_request(text)
    u=message(text);c=context()
    assert not asyncio.run(client.handle_message(u,c,1,'fake-password'))
    env.schema_available.assert_not_called();client.interpreter.interpret.assert_not_called()
    assert not c.user_data


@pytest.mark.parametrize('text',['Aggiungi cliente Marco Rossi e prenota domani',
                                  'Inserisci cliente Marco Rossi e cancella appuntamento'])
def test_mixed_request_never_reaches_either_ai(env,text):
    run(message(text),context())
    client.interpreter.interpret.assert_not_called();ai.interpreter.interpret.assert_not_called()
    env.add_client.assert_not_called()


def test_unauthorized_no_state_or_services(env):
    u=message(user=9);c=context();run(u,c)
    assert not c.user_data;env.schema_available.assert_not_called()
    u.effective_message.reply_text.assert_not_awaited()


@pytest.mark.parametrize('missing', ['first_name','last_name','both'])
def test_missing_fields_keep_request_key_no_more_ai(env,monkeypatch,missing):
    data=replace(INTENT,first_name=None if missing in ('first_name','both') else 'Marco',
                 last_name=None if missing in ('last_name','both') else 'Rossi')
    monkeypatch.setattr(client.interpreter,'interpret',Mock(return_value=data))
    c=context();run(message(),c);env.add_client.assert_not_called()
    idx=4
    if missing in ('first_name','both'):
        run(message('Marco',idx),c);idx+=1
    if missing in ('last_name','both'):run(message('Rossi',idx),c)
    env.add_client.assert_called_once()
    assert env.add_client.call_args.kwargs['request_key']=='client:2:3'
    assert env.add_client.call_args.kwargs['first_name']=='Marco'
    assert env.add_client.call_args.kwargs['last_name']=='Rossi'
    client.interpreter.interpret.assert_called_once()


def test_bad_field_keeps_plan_without_writing(env,monkeypatch):
    monkeypatch.setattr(client.interpreter,'interpret',Mock(return_value=replace(INTENT,last_name=None)))
    c=context();run(message(),c);run(message('x'*101,4),c)
    assert c.user_data['ai_clients'][2].stage=='collect'
    env.add_client.assert_not_called()


@pytest.mark.parametrize('answer',['sì','si','confermo','Confermo omonimo','confermo omonimo.'])
def test_generic_confirmation_never_authorizes(env,answer):
    env.add_client.return_value=ClientResult(ClientStatus.HOMONYM_REQUIRES_CONFIRMATION)
    c=context();run(message(),c);run(message(answer,4),c)
    assert env.add_client.call_count==1 and c.user_data['ai_clients'][2].stage=='confirm'


def test_exact_confirmation_single_use_and_same_key(env):
    env.add_client.side_effect=[ClientResult(ClientStatus.HOMONYM_REQUIRES_CONFIRMATION),ClientResult(ClientStatus.CREATED,13)]
    c=context();run(message(),c);token=c.user_data['ai_clients'][2].token
    run(message('confermo omonimo',4),c);run(message('confermo omonimo',4),c)
    assert env.add_client.call_count==2
    calls=env.add_client.call_args_list
    assert calls[0].kwargs['allow_homonym'] is False and calls[1].kwargs['allow_homonym'] is True
    assert calls[0].kwargs['request_key']==calls[1].kwargs['request_key']=='client:2:3'
    assert not c.user_data['ai_clients']
    assert token
    ai.interpreter.interpret.assert_not_called()


def test_confirmation_must_follow_delivered_prompt(env):
    env.add_client.return_value=ClientResult(ClientStatus.HOMONYM_REQUIRES_CONFIRMATION)
    c=context();u=message();u.effective_message.reply_text.return_value=SimpleNamespace(message_id=10)
    run(u,c);run(message('confermo omonimo',9),c)
    assert env.add_client.call_count==1


@pytest.mark.parametrize('kind',['question','confirmation'])
def test_undelivered_prompt_invalidates_only_client_session(env,monkeypatch,kind):
    if kind=='question':monkeypatch.setattr(client.interpreter,'interpret',Mock(return_value=replace(INTENT,last_name=None)))
    else:env.add_client.return_value=ClientResult(ClientStatus.HOMONYM_REQUIRES_CONFIRMATION)
    c=context();c.user_data['unrelated']=object();sentinel=c.user_data['unrelated']
    u=message();u.effective_message.reply_text.side_effect=RuntimeError('private details');run(u,c)
    assert not c.user_data['ai_clients'] and c.user_data['unrelated'] is sentinel
    run(message('confermo omonimo',4),c)
    assert env.add_client.call_count==(0 if kind=='question' else 1)


@pytest.mark.parametrize('status',[ClientStatus.CREATED,ClientStatus.ALREADY_EXISTS,ClientStatus.REQUEST_CONFLICT,ClientStatus.UNAVAILABLE,ClientStatus.UNKNOWN])
def test_outcomes_and_delivery_failure_no_retry(env,status):
    env.add_client.return_value=ClientResult(status,12 if status in (ClientStatus.CREATED,ClientStatus.ALREADY_EXISTS) else None)
    c=context();u=message();u.effective_message.reply_text.side_effect=RuntimeError('private details');run(u,c)
    env.add_client.assert_called_once()
    if status is ClientStatus.UNKNOWN:
        assert c.user_data['ai_clients'][2].stage=='uncertain'
        run(message('confermo omonimo',4),c)
        env.add_client.assert_called_once()
    else:assert not c.user_data['ai_clients']


def test_uncertain_original_never_retried_after_cancel(env):
    env.add_client.return_value=ClientResult(ClientStatus.UNKNOWN)
    c=context();run(message(),c);run(message('annulla',4),c);run(message(),c)
    env.add_client.assert_called_once();client.interpreter.interpret.assert_called_once()


@pytest.mark.parametrize('stage',['collect','confirm','uncertain'])
def test_cancel_and_expire(env,monkeypatch,stage):
    if stage=='collect':monkeypatch.setattr(client.interpreter,'interpret',Mock(return_value=replace(INTENT,last_name=None)))
    elif stage=='confirm':env.add_client.return_value=ClientResult(ClientStatus.HOMONYM_REQUIRES_CONFIRMATION)
    else:env.add_client.return_value=ClientResult(ClientStatus.UNKNOWN)
    c=context();run(message(),c);count=env.add_client.call_count
    session=c.user_data['ai_clients'][2];session.expires=0
    run(message('confermo omonimo',4),c)
    assert not c.user_data['ai_clients'] and env.add_client.call_count==count
    c.user_data['ai_clients'][2]=session
    u=message('annulla',5);u.effective_message.reply_text.side_effect=RuntimeError()
    run(u,c);assert not c.user_data['ai_clients'] and env.add_client.call_count==count


@pytest.mark.parametrize('stage',['collect','confirm','uncertain'])
def test_pending_clients_block_new_appointment_and_client_requests(env,monkeypatch,stage):
    if stage=='collect':monkeypatch.setattr(client.interpreter,'interpret',Mock(return_value=replace(INTENT,last_name=None)))
    elif stage=='confirm':env.add_client.return_value=ClientResult(ClientStatus.HOMONYM_REQUIRES_CONFIRMATION)
    else:env.add_client.return_value=ClientResult(ClientStatus.UNKNOWN)
    c=context();run(message(),c);session=c.user_data['ai_clients'][2]
    count=env.add_client.call_count
    for i,text in enumerate(('Prenota Marco Rossi domani','Aggiungi cliente Luca Bianchi'),4):run(message(text,i),c)
    assert c.user_data['ai_clients'][2] is session
    assert env.add_client.call_count==count
    ai.interpreter.interpret.assert_not_called()


@pytest.mark.parametrize('kind',['collect','creation_confirm','creation_overlap','move_collect','move_select','move_confirm','move_overlap','delete_collect','delete_select','delete_confirm'])
def test_all_appointment_procedures_preserved(env,kind):
    c=context()
    if kind=='collect':ai.conversations[(1,2)]=SimpleNamespace(current_intent=object())
    elif kind.startswith('creation'):
        c.user_data['pending_ai_bookings']={'token':SimpleNamespace(user_id=1,chat_id=2,phase=kind)}
    else:
        key='ai_move' if kind.startswith('move') else 'ai_delete'
        c.user_data[key]=dict(user_id=1,chat_id=2,stage=kind)
    before=c.user_data.copy();conversation=ai.conversations.copy()
    run(message(),c)
    assert c.user_data==before and ai.conversations==conversation
    env.schema_available.assert_not_called();client.interpreter.interpret.assert_not_called()


def test_other_chat_not_consumed_by_client_session(env,monkeypatch):
    monkeypatch.setattr(client.interpreter,'interpret',Mock(return_value=replace(INTENT,last_name=None)))
    c=context();run(message(),c)
    assert not asyncio.run(client.handle_message(message('Rossi',4,chat=9),c,1,'unused'))
    assert c.user_data['ai_clients'][2].intent.last_name is None
    run(message(chat=9,message_id=5),c)
    assert set(c.user_data['ai_clients'])=={2,9}
    assert not asyncio.run(client.handle_message(message('Rossi',6,user=7),c,1,'unused'))
    env.add_client.assert_not_called()


def test_schema_missing_does_not_call_ai_or_insert(env):
    env.schema_available.return_value=False;c=context();u=message();run(u,c)
    client.interpreter.interpret.assert_not_called();env.add_client.assert_not_called()
    assert 'non ancora disponibile' in u.effective_message.reply_text.call_args.args[0]


@pytest.mark.parametrize('error',[ClientResponseError('invalid'),UnsupportedClientIntent('unsupported'),
    OllamaError(OllamaErrorKind.CONNECTION),OllamaError(OllamaErrorKind.TIMEOUT),
    OllamaError(OllamaErrorKind.HTTP),OllamaError(OllamaErrorKind.INVALID_RESPONSE)])
def test_ai_failure_no_fallback_or_write(env,monkeypatch,error):
    monkeypatch.setattr(client.interpreter,'interpret',Mock(side_effect=error))
    c=context();run(message(),c)
    env.add_client.assert_not_called();ai.interpreter.interpret.assert_not_called()
    assert not c.user_data['ai_clients']


def test_duplicate_initial_message_does_not_restart_pending_plan(env,monkeypatch):
    monkeypatch.setattr(client.interpreter,'interpret',Mock(return_value=replace(INTENT,last_name=None)))
    c=context();run(message(),c);session=c.user_data['ai_clients'][2];run(message(),c)
    assert c.user_data['ai_clients'][2] is session
    client.interpreter.interpret.assert_called_once();env.add_client.assert_not_called()


@pytest.mark.parametrize('phase',['inference','insert'])
def test_cancellation_keeps_lock_until_worker_finishes(env,monkeypatch,phase):
    started,release,finished=threading.Event(),threading.Event(),threading.Event()
    def work(*args,**kwargs):
        started.set()
        try:
            assert release.wait(2)
            return INTENT if phase=='inference' else ClientResult(ClientStatus.CREATED,12)
        finally:finished.set()
    if phase=='inference':monkeypatch.setattr(client.interpreter,'interpret',work)
    else:env.add_client.side_effect=work
    c=context();u=message()
    async def scenario():
        task=asyncio.create_task(ai.handle_ai_message(u,c,1,'test-calendar','fake-password'))
        try:
            for _ in range(500):
                if started.is_set():break
                await asyncio.sleep(.001)
            assert started.is_set();task.cancel();await asyncio.sleep(.01)
            assert not task.done() and ai.conversation_locks.locked()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):await task
        assert finished.is_set() and not ai.conversation_locks.locked()
    asyncio.run(scenario())
    if phase=='insert':assert c.user_data['ai_clients'][2].stage=='uncertain'
    else:env.add_client.assert_not_called()
    u.effective_message.reply_text.assert_not_awaited()


@pytest.mark.parametrize('text',['Vorrei prenotare Marco domani','Voglio un appuntamento per Marco',
                                  'Puoi spostare l’allenamento di Marco?','confermo omonimo'])
def test_procedure_text_is_never_saved_as_missing_surname(env,monkeypatch,text):
    monkeypatch.setattr(client.interpreter,'interpret',Mock(return_value=replace(INTENT,last_name=None)))
    c=context();run(message(),c);session=c.user_data['ai_clients'][2]
    run(message(text,4),c)
    assert c.user_data['ai_clients'][2] is session and session.intent.last_name is None
    env.add_client.assert_not_called();ai.interpreter.interpret.assert_not_called()


@pytest.mark.parametrize('row,available', [(('trainers','clients','operations'),True),((None,'clients','operations'),False),
                                         (('trainers',None,'operations'),False),((None,None,None),False),
                                         (('trainers','clients',None),False)])
def test_schema_probe_read_only_and_metadata_only(row,available):
    from unittest.mock import MagicMock
    from client_service import ClientService
    connection=MagicMock();connection.__enter__.return_value=connection
    cursor=connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value=row
    factory=Mock(return_value=connection)
    service=ClientService('unused',connection_factory=factory)
    assert service.schema_available() is available
    statements=[call.args[0] for call in cursor.execute.call_args_list]
    assert statements==['SET TRANSACTION READ ONLY',
                       "SELECT to_regclass('public.trainers'), to_regclass('public.clients'), to_regclass('public.client_operations')"]
    connection.__exit__.assert_called_once()


def test_schema_probe_failure_is_sanitized():
    from client_service import ClientService
    service=ClientService('unused',connection_factory=Mock(side_effect=RuntimeError('private credentials')))
    assert service.schema_available() is False


def test_client_insert_exception_blocks_replay(env):
    env.add_client.side_effect=RuntimeError('private commit details')
    c=context();u=message();run(u,c)
    assert c.user_data['ai_clients'][2].stage=='uncertain'
    run(message('confermo omonimo',4),c)
    env.add_client.assert_called_once()
    assert 'private' not in u.effective_message.reply_text.call_args.args[0]


def test_schema_check_happens_before_inference_without_open_transaction(env,monkeypatch):
    order=[]
    env.schema_available.side_effect=lambda:order.append('schema_closed') or True
    monkeypatch.setattr(client.interpreter,'interpret',Mock(side_effect=lambda text:order.append('ai') or INTENT))
    env.add_client.side_effect=lambda **kwargs:order.append('commit_closed') or ClientResult(ClientStatus.CREATED,12)
    u=message()
    async def send(*args,**kwargs):order.append('telegram')
    u.effective_message.reply_text.side_effect=send
    run(u,context())
    assert order==['schema_closed','ai','commit_closed','telegram']


def test_successful_redelivery_uses_same_request_key_and_dedup_result(env):
    env.add_client.side_effect=[ClientResult(ClientStatus.CREATED,12),ClientResult(ClientStatus.ALREADY_EXISTS,12)]
    c=context();run(message(),c);u=message();run(u,c)
    assert [call.kwargs['request_key'] for call in env.add_client.call_args_list]==['client:2:3']*2
    assert 'già registrata' in u.effective_message.reply_text.call_args.args[0]


def test_cancelled_confirmation_delivery_invalidates_plan(env):
    env.add_client.return_value=ClientResult(ClientStatus.HOMONYM_REQUIRES_CONFIRMATION)
    u=message();c=context();u.effective_message.reply_text.side_effect=asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):run(u,c)
    assert not c.user_data['ai_clients']
    assert env.add_client.call_count==1
    run(message('confermo omonimo',4),c)
    assert env.add_client.call_count==1


@pytest.mark.parametrize('status',[ClientStatus.UNRESOLVED,ClientStatus.ALREADY_EXISTS,ClientStatus.UNAVAILABLE])
def test_durable_receipt_checked_before_ai_with_empty_telegram_memory(env,status):
    env.lookup_request.return_value=ClientResult(status,12 if status is ClientStatus.ALREADY_EXISTS else None)
    c=context();run(message(),c)
    client.interpreter.interpret.assert_not_called();env.add_client.assert_not_called()
    env.lookup_request.assert_called_once_with(authorized_telegram_user_id=1,request_key='client:2:3')
    if status is ClientStatus.ALREADY_EXISTS:
        assert not c.user_data['ai_client_uncertain']


@pytest.mark.parametrize('commit_mode',['before','after'])
def test_restart_after_final_commit_uncertainty_uses_durable_receipt(env,monkeypatch,commit_mode):
    from test_client_service import Store
    from client_service import ClientService
    store=Store();store.fail_commit_at=3;store.commit_error=commit_mode
    service=ClientService('unused',connection_factory=store.connect)
    monkeypatch.setattr(client,'ClientService',Mock(return_value=service))
    # This test exercises schema-independent service/handler integration; schema
    # presence itself is covered by the metadata-query contract tests.
    monkeypatch.setattr(service,'schema_available',Mock(return_value=True))
    c=context();run(message(),c)
    assert c.user_data['ai_clients'][2].stage=='uncertain'
    store.commit_error=None
    client.interpreter.interpret.reset_mock()
    before=len(store.clients)
    restarted=context()
    run(message(),restarted)
    client.interpreter.interpret.assert_not_called()
    assert len(store.clients)==before
    if commit_mode=='before':assert store.operations[0][5]=='PENDING' and not store.clients
    else:assert store.operations[0][5]=='CREATED' and len(store.clients)==1


def test_new_message_same_name_after_restart_cannot_bypass_pending(env,monkeypatch):
    from test_client_service import Store
    from client_service import ClientService
    store=Store();store.fail_commit_at=3;store.commit_error='before'
    service=ClientService('unused',connection_factory=store.connect)
    monkeypatch.setattr(client,'ClientService',Mock(return_value=service))
    monkeypatch.setattr(service,'schema_available',Mock(return_value=True))
    run(message(),context())
    store.commit_error=None
    c=context();run(message(message_id=99),c)
    assert not store.clients and len(store.operations)==1
    assert c.user_data['ai_clients'][2].stage=='uncertain'
    run(message('confermo omonimo',100),c)
    assert not store.clients


def test_new_message_after_committed_unknown_requires_homonym_confirmation(env,monkeypatch):
    from test_client_service import Store
    from client_service import ClientService
    store=Store();store.fail_commit_at=3;store.commit_error='after'
    service=ClientService('unused',connection_factory=store.connect)
    monkeypatch.setattr(client,'ClientService',Mock(return_value=service))
    monkeypatch.setattr(service,'schema_available',Mock(return_value=True))
    run(message(),context())
    assert len(store.clients)==1 and store.operations[0][5]=='CREATED'
    store.commit_error=None
    c=context();run(message(message_id=99),c)
    assert len(store.clients)==1 and len(store.operations)==1
    assert c.user_data['ai_clients'][2].stage=='confirm'


@pytest.mark.parametrize('mode',['before','after'])
def test_initial_commit_uncertain_never_starts_client_transaction(env,monkeypatch,mode):
    from test_client_service import Store
    from client_service import ClientService
    # The preceding read-only receipt lookup is transaction 1.
    store=Store();store.fail_commit_at=2;store.commit_error=mode
    service=ClientService('unused',connection_factory=store.connect)
    monkeypatch.setattr(client,'ClientService',Mock(return_value=service))
    monkeypatch.setattr(service,'schema_available',Mock(return_value=True))
    c=context();run(message(),c)
    assert c.user_data['ai_clients'][2].stage=='uncertain'
    assert len(store.connections)==2 and not store.clients
    assert not any(sql.startswith('INSERT INTO public.clients') for connection in store.connections
                   for sql,_ in connection.queries)
    assert len(store.operations)==(1 if mode=='after' else 0)


@pytest.mark.parametrize('text',[
    'Crea un nuovo cliente di nome Marco Rossi',
    '  CREA un nuovo CLIENTE di nome Marco Rossi',
    'Crea clienti',
])
def test_crea_client_routes_only_to_clients(env,text):
    assert client.is_client_request(text)
    run(message(text),context())
    env.add_client.assert_called_once()
    client.interpreter.interpret.assert_called_once()
    ai.interpreter.interpret.assert_not_called()
    ai.check_ai_availability.assert_not_called()


@pytest.mark.parametrize('text',['Crea un appuntamento domani','Crea Marco Rossi','Crea clientela'])
def test_crea_requires_explicit_client_reference(env,text):
    assert not client.is_client_request(text)
    assert not asyncio.run(client.handle_message(message(text),context(),1,'unused'))
    client.interpreter.interpret.assert_not_called()
    env.add_client.assert_not_called()


def test_crea_appointment_reaches_existing_appointment_interpreter(env,monkeypatch):
    from ai.schemas import AppointmentIntent
    monkeypatch.setattr(ai.interpreter,'interpret',Mock(return_value=AppointmentIntent('crea',data='2026-11-30')))
    run(message('Crea un appuntamento domani'),context())
    ai.interpreter.interpret.assert_called_once_with('Crea un appuntamento domani')
    client.interpreter.interpret.assert_not_called()
    env.add_client.assert_not_called()


@pytest.mark.parametrize('kind',['collect','creation_confirm','creation_overlap','move_collect',
    'move_select','move_confirm','move_overlap','delete_collect','delete_select','delete_confirm'])
def test_crea_client_does_not_alter_pending_appointment(env,kind):
    c=context()
    if kind=='collect':ai.conversations[(1,2)]=SimpleNamespace(current_intent=object())
    elif kind.startswith('creation'):
        c.user_data['pending_ai_bookings']={'token':SimpleNamespace(user_id=1,chat_id=2,phase=kind)}
    else:
        key='ai_move' if kind.startswith('move') else 'ai_delete'
        c.user_data[key]=dict(user_id=1,chat_id=2,stage=kind)
    before=c.user_data.copy();conversations=ai.conversations.copy()
    run(message('Crea un nuovo cliente di nome Marco Rossi'),c)
    assert c.user_data==before and ai.conversations==conversations
    env.schema_available.assert_not_called();env.add_client.assert_not_called()
    client.interpreter.interpret.assert_not_called();ai.interpreter.interpret.assert_not_called()


@pytest.mark.parametrize('text',[
    'Crea un nuovo cliente di nome Marco Rossi e prenota domani',
    'Crea un nuovo cliente di nome Marco Rossi e crea un appuntamento domani',
    'Crea cliente Marco Rossi e appuntamenti domani',
])
def test_crea_mixed_request_no_write_or_fallback(env,text):
    assert client.is_client_request(text)
    run(message(text),context())
    env.schema_available.assert_not_called();env.add_client.assert_not_called()
    client.interpreter.interpret.assert_not_called();ai.interpreter.interpret.assert_not_called()
