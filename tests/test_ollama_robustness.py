"""Offline Ollama HTTP, inference boundaries and protected cancellation."""
import asyncio
import json
from pathlib import Path
import sys
import threading
from unittest.mock import AsyncMock, Mock
from types import SimpleNamespace

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'telegram-bot'))
from ai.ollama_client import OllamaClient, OllamaError, OllamaErrorKind as Kind
from ai.interpreter import AppointmentInterpreter, ModelResponseError, UnknownIntentError
from ai.conversation import ConversationManager
from ai.schemas import AppointmentIntent
import ai_handler as h
from test_ai_creation_confirmation import update

SECRET = 'private-model-content'
VALID_INTENT = {'operazione':'crea','cliente':'Fake','data':'2026-11-30','ora':'15:00','durata_minuti':30}


@pytest.fixture(autouse=True)
def no_real_http(monkeypatch):
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', Mock(side_effect=AssertionError('Real HTTP forbidden')))


def install_http(monkeypatch, *, body=None, status=200, error=None, raw=None):
    requests=[]
    def handle(request):
        requests.append(request)
        if error:
            raise error(SECRET, request=request)
        if raw is not None:
            return httpx.Response(status, content=raw)
        return httpx.Response(status,json=body,headers={'Location':'http://127.0.0.1:11434/redirect-target'})
    def post(url, **kwargs):
        assert kwargs['follow_redirects'] is False
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            return client.post(url, **kwargs)
    monkeypatch.setattr(httpx,'post',post)
    return requests


def test_success_timeout_payload_and_single_request(monkeypatch):
    calls=install_http(monkeypatch,body={'done':True,'message':{'content':json.dumps(VALID_INTENT)}})
    result=AppointmentInterpreter(OllamaClient()).interpret('Prenota Fake')
    assert result.operazione=='crea' and result.ora=='15:00'
    assert len(calls)==1
    request=calls[0]
    assert request.extensions['timeout']=={'connect':5.0,'read':60.0,'write':60.0,'pool':60.0}
    payload=json.loads(request.content)
    assert payload['model']=='qwen2.5:3b' and payload['stream'] is False
    assert payload['options']=={'num_predict':256}


@pytest.mark.parametrize('error,kind',[
    (httpx.ConnectError,Kind.CONNECTION), (httpx.ConnectTimeout,Kind.TIMEOUT),
    (httpx.ReadTimeout,Kind.TIMEOUT), (httpx.RemoteProtocolError,Kind.CONNECTION),
])
def test_http_errors_are_categorized_without_retry(monkeypatch,error,kind):
    calls=install_http(monkeypatch,error=error)
    with pytest.raises(OllamaError) as caught:OllamaClient().chat([])
    assert caught.value.kind is kind and SECRET not in str(caught.value)
    assert len(calls)==1


@pytest.mark.parametrize('status',[301,302,307,308,404,500,503])
def test_service_status_no_retry_or_redirect(monkeypatch,status):
    calls=install_http(monkeypatch,status=status,body={'error':SECRET})
    with pytest.raises(OllamaError) as caught:OllamaClient().chat([])
    assert caught.value.kind is Kind.HTTP
    assert len(calls)==1 and SECRET not in str(caught.value)


@pytest.mark.parametrize('body',[
    None,[],{}, {'done':True}, {'done':True,'message':None},
    {'done':True,'message':[]}, {'done':True,'message':{}},
    {'done':True,'message':{'content':None}}, {'done':True,'message':{'content':123}},
    {'done':True,'message':{'content':''}}, {'done':True,'message':{'content':'  '}},
    {'message':{'content':'{}'}}, {'done':False,'message':{'content':'{}'}},
    {'done':1,'message':{'content':'{}'}},
])
def test_incomplete_response_refused(monkeypatch,body):
    calls=install_http(monkeypatch,body=body)
    with pytest.raises(OllamaError) as caught:OllamaClient().chat([])
    assert caught.value.kind is Kind.INVALID_RESPONSE and len(calls)==1


def test_invalid_http_json(monkeypatch):
    calls=install_http(monkeypatch,raw=b'not-json')
    with pytest.raises(OllamaError) as caught:OllamaClient().chat([])
    assert caught.value.kind is Kind.INVALID_RESPONSE and len(calls)==1


@pytest.mark.parametrize('content,error',[
    ('not-json '+SECRET,ModelResponseError),
    ('[]',ModelResponseError),
    (json.dumps({'operazione':SECRET}),UnknownIntentError),
    (json.dumps({'cliente':'Fake'}),UnknownIntentError),
    (json.dumps({**VALID_INTENT,'ora':'99:99'}),ModelResponseError),
    (json.dumps({**VALID_INTENT,'data':SECRET}),ModelResponseError),
])
def test_model_validation_is_scoped(content,error):
    client=Mock();client.chat.return_value=content
    with pytest.raises(error) as caught:AppointmentInterpreter(client).interpret('request')
    assert SECRET not in str(caught.value)
    client.chat.assert_called_once()


def test_unrelated_valueerror_is_not_model_error():
    with pytest.raises(ValueError) as caught:AppointmentInterpreter(Mock()).interpret('')
    assert not isinstance(caught.value,ModelResponseError)


ERROR_MESSAGES=[
    (OllamaError(Kind.CONNECTION),"L'assistente non è disponibile. Nessuna operazione avviata."),
    (OllamaError(Kind.TIMEOUT),"L'assistente sta impiegando troppo tempo. Nessuna operazione avviata."),
    (OllamaError(Kind.HTTP),"L'assistente non riesce a rispondere. Nessuna operazione avviata."),
    (OllamaError(Kind.INVALID_RESPONSE),'Non sono riuscito a interpretare la richiesta. Prova a riformularla.'),
    (ModelResponseError('invalid'), 'Non sono riuscito a interpretare la richiesta. Prova a riformularla.'),
    (UnknownIntentError('unknown'), "Non ho riconosciuto l'operazione. Specifica se vuoi prenotare, spostare o cancellare."),
]


@pytest.mark.parametrize('error,text',ERROR_MESSAGES)
@pytest.mark.parametrize('change_intent',[False,True])
def test_inference_failure_never_reaches_operations(monkeypatch,capsys,error,text,change_intent):
    u=update(text='cancella appuntamento Fake' if change_intent else 'prenota Fake')
    c=SimpleNamespace(user_data={});other=object()
    state={(99,99):other}
    if change_intent:
        manager=ConversationManager();manager.start(AppointmentIntent('crea'),'original-key');state[(1,2)]=manager
    monkeypatch.setattr(h,'conversations',state)
    monkeypatch.setattr(h,'conversation_locks',asyncio.Lock())
    interpret=Mock(side_effect=error);monkeypatch.setattr(h.interpreter,'interpret',interpret)
    availability=Mock(side_effect=AssertionError('No availability after inference error'))
    connection=Mock(side_effect=AssertionError('No database after inference error'))
    process=Mock(side_effect=AssertionError('No write after inference error'))
    monkeypatch.setattr(h,'check_ai_availability',availability)
    monkeypatch.setattr(h,'get_connection',connection)
    monkeypatch.setattr(h,'process_booking',process)
    asyncio.run(h.handle_ai_message(u,c,1,'test-calendar','unused'))
    u.effective_message.reply_text.assert_awaited_once_with(text)
    assert h.conversations=={(99,99):other}
    interpret.assert_called_once();availability.assert_not_called();connection.assert_not_called();process.assert_not_called()
    assert SECRET not in capsys.readouterr().out


def test_delivery_failure_does_not_restart_inference(monkeypatch):
    u=update();c=SimpleNamespace(user_data={})
    u.effective_message.reply_text.side_effect=RuntimeError(SECRET)
    monkeypatch.setattr(h,'conversations',{})
    monkeypatch.setattr(h,'conversation_locks',asyncio.Lock())
    infer=Mock(side_effect=OllamaError(Kind.TIMEOUT));monkeypatch.setattr(h.interpreter,'interpret',infer)
    asyncio.run(h.handle_ai_message(u,c,1,'test-calendar','unused'))
    infer.assert_called_once();u.effective_message.reply_text.assert_awaited_once()
    assert not h.conversations


@pytest.mark.parametrize('change_intent',[False,True])
def test_cancelled_inference_finishes_under_lock(monkeypatch,change_intent):
    u=update(text='cancella appuntamento Fake' if change_intent else 'prenota Fake')
    c=SimpleNamespace(user_data={});started,release,finished=threading.Event(),threading.Event(),threading.Event()
    state={};manager=None
    if change_intent:
        manager=ConversationManager();manager.start(AppointmentIntent('crea'),'original-key');state[(1,2)]=manager
    monkeypatch.setattr(h,'conversations',state)
    monkeypatch.setattr(h,'conversation_locks',asyncio.Lock())
    def infer(*args):
        started.set()
        try:
            assert release.wait(2)
            return AppointmentIntent('cancella',cliente='Fake',data='2026-11-30',ora='15:00')
        finally:finished.set()
    monkeypatch.setattr(h.interpreter,'interpret',infer)
    downstream=AsyncMock(side_effect=AssertionError('No downstream operation after cancellation'))
    monkeypatch.setattr(h,'send_ai_result',downstream)
    async def scenario():
        task=asyncio.create_task(h.handle_ai_message(u,c,1,'test-calendar','unused'))
        try:
            for _ in range(500):
                if started.is_set():break
                await asyncio.sleep(.001)
            assert started.is_set();task.cancel();await asyncio.sleep(.01)
            task.cancel();await asyncio.sleep(.01)
            assert not task.done() and h.conversation_locks.locked()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):await task
        assert finished.is_set() and not h.conversation_locks.locked()
        assert (1,2) not in h.conversations
        if manager:assert manager.current_intent.operazione=='cancella'
        snapshot=dict(h.conversations);await asyncio.sleep(.01)
        assert h.conversations==snapshot
    asyncio.run(scenario());downstream.assert_not_awaited()
    u.effective_message.reply_text.assert_not_awaited()


def test_other_valueerror_is_not_relabelled_as_model_error(monkeypatch,capsys):
    u=update();c=SimpleNamespace(user_data={})
    monkeypatch.setattr(h,'conversations',{})
    monkeypatch.setattr(h,'conversation_locks',asyncio.Lock())
    monkeypatch.setattr(h.interpreter,'interpret',Mock(side_effect=ValueError(SECRET)))
    asyncio.run(h.handle_ai_message(u,c,1,'test-calendar','unused'))
    text=u.effective_message.reply_text.call_args.args[0]
    assert 'Impossibile preparare la richiesta' in text
    assert 'interpretare' not in text
    assert SECRET not in capsys.readouterr().out
