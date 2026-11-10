"""Offline structured extraction, with no Ollama, database or Google access."""
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ai.client_interpreter import ClientInterpreter, CLIENT_SYSTEM_PROMPT
from ai.client_schemas import (
    ClientIntent, ClientResponseError, UnsupportedClientIntent, LIMITS, validate_client_intent,
)
from ai.ollama_client import OllamaClient, OllamaError, OllamaErrorKind


@pytest.fixture(autouse=True)
def no_real_http(monkeypatch):
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request',
                        Mock(side_effect=AssertionError('Real HTTP forbidden')))


def interpreter_for(data):
    client = Mock()
    client.chat.return_value = json.dumps(data)
    return ClientInterpreter(client), client


@pytest.mark.parametrize('text,data', [
    ('Aggiungi Marco Rossi come cliente', {'first_name':'Marco','last_name':'Rossi'}),
    ('Registra Luca Bianchi, telefono 3331234567',
     {'first_name':'Luca','last_name':'Bianchi','phone':'3331234567'}),
    ('Inserisci un nuovo cliente di nome Mario Rossi', {'first_name':'Mario','last_name':'Rossi'}),
    ('Aggiungi cliente: nome Gian Luca, cognome De Santis',
     {'first_name':'Gian Luca','last_name':'De Santis'}),
    ('Registra cliente: nome Éléna-Maria, cognome D’Angelo',
     {'first_name':'Éléna-Maria','last_name':'D’Angelo'}),
    ("Registra cliente: nome Anna Maria, cognome D'Onofrio-Rossi",
     {'first_name':'Anna Maria','last_name':"D'Onofrio-Rossi"}),
])
def test_extraction_and_composed_names(text, data):
    interpreter, client = interpreter_for({'operation':'add_client', **data})
    result = interpreter.interpret(text)
    for field, value in data.items():
        assert getattr(result, field) == value
    assert result.missing_fields == ()
    assert result.operation == 'add_client'
    client.chat.assert_called_once_with([
        {'role':'system','content':CLIENT_SYSTEM_PROMPT},
        {'role':'user','content':text},
    ])


def test_all_fields_and_phone_preserved_as_text():
    data = dict(operation='add_client', first_name='Mario', last_name='Rossi',
                phone='+39 0333 1234567', email='fake@example.invalid', notes='Solo mattina.')
    interpreter, _ = interpreter_for(data)
    result = interpreter.interpret(
        'Registra Mario Rossi, telefono +39 0333 1234567, email fake@example.invalid, note Solo mattina.')
    assert result.phone == '+39 0333 1234567'
    assert result.email == 'fake@example.invalid' and result.notes == 'Solo mattina.'
    with pytest.raises(FrozenInstanceError):
        result.first_name = 'other'


@pytest.mark.parametrize('data,source,missing', [
    ({'first_name':'Marco'}, 'Aggiungi Marco come cliente', ('last_name',)),
    ({'last_name':'Rossi'}, 'Aggiungi cliente con cognome Rossi', ('first_name',)),
    ({}, 'Aggiungi un cliente', ('first_name','last_name')),
    ({'first_name':None,'last_name':None}, 'Aggiungi un cliente', ('first_name','last_name')),
])
def test_missing_fields_for_future_handler(data, source, missing):
    interpreter, _ = interpreter_for({'operation':'add_client', **data})
    result = interpreter.interpret(source)
    assert result.missing_fields == missing
    assert result.phone is result.email is result.notes is None


@pytest.mark.parametrize('operation', ['unsupported','crea','modifica','cancella','create_booking'])
def test_appointment_intents_are_not_client_intents(operation):
    interpreter, _ = interpreter_for({'operation':operation})
    with pytest.raises(UnsupportedClientIntent):
        interpreter.interpret('Prenota un appuntamento per Marco Rossi domani')


@pytest.mark.parametrize('data', [None,[],42,True,'text',{}, {'operation':None},
                                {'operation':42}, {'operation':True}])
def test_invalid_shape_or_missing_discriminator(data):
    interpreter, _ = interpreter_for(data)
    with pytest.raises(ClientResponseError):interpreter.interpret('Aggiungi un cliente')


@pytest.mark.parametrize('field', ['trainer_id','telegram_user_id','authorized_telegram_user_id',
                                  'request_key','allow_homonym','client_id','data','ora','cliente','operazione'])
def test_untrusted_fields_rejected(field):
    interpreter, _ = interpreter_for({'operation':'add_client',field:'untrusted'})
    with pytest.raises(ClientResponseError):interpreter.interpret('Aggiungi un cliente')


@pytest.mark.parametrize('field', list(LIMITS))
@pytest.mark.parametrize('value', [False,True,123,[],{},'', ' \n\t', 'bad\x00value'])
def test_rigorous_field_validation(field, value):
    with pytest.raises(ClientResponseError):
        validate_client_intent({'operation':'add_client',field:value})


@pytest.mark.parametrize('field,limit', list(LIMITS.items()))
def test_field_limits(field, limit):
    result = validate_client_intent({'operation':'add_client',field:'x'*limit})
    assert getattr(result,field) == 'x'*limit
    with pytest.raises(ClientResponseError):
        validate_client_intent({'operation':'add_client',field:'x'*(limit+1)})


@pytest.mark.parametrize('response', [
    '', 'not JSON', '```json\n{}\n```', '{} {}',
    '{"operation":"add_client","operation":"unsupported"}',
    '{"operation":"add_client","phone":NaN}',
    '{"operation":"add_client","phone":Infinity}',
    '{"operation":"add_client","phone":-Infinity}',
])
def test_invalid_json_and_duplicate_keys(response):
    client = Mock();client.chat.return_value = response
    with pytest.raises(ClientResponseError):ClientInterpreter(client).interpret('Aggiungi un cliente')
    client.chat.assert_called_once()


@pytest.mark.parametrize('value', [None,123,True,{},[]])
def test_nontext_response(value):
    client = Mock();client.chat.return_value = value
    with pytest.raises(ClientResponseError):ClientInterpreter(client).interpret('Aggiungi un cliente')


@pytest.mark.parametrize('kind', list(OllamaErrorKind))
def test_technical_ollama_errors_propagate_without_retry(kind):
    client = Mock();error = OllamaError(kind);client.chat.side_effect = error
    with pytest.raises(OllamaError) as caught:ClientInterpreter(client).interpret('Aggiungi Marco Rossi')
    assert caught.value is error
    client.chat.assert_called_once()


@pytest.mark.parametrize('text', [None,False,42,'','  ','bad\x00input'])
def test_invalid_input_never_calls_model(text):
    client = Mock()
    with pytest.raises(ValueError):ClientInterpreter(client).interpret(text)
    client.chat.assert_not_called()


@pytest.mark.parametrize('data', [
    {'first_name':'Mario','last_name':'Inventato'},
    {'first_name':'Marco','last_name':'Rossi','phone':'3339999999'},
    {'first_name':'Marco','last_name':'Rossi','email':'invented@example.invalid'},
    {'first_name':'Marco','last_name':'Rossi','notes':'Preferisce il mattino'},
])
def test_values_without_source_evidence_rejected_without_sensitive_error(data,capsys):
    interpreter, _ = interpreter_for({'operation':'add_client',**data})
    with pytest.raises(ClientResponseError) as caught:interpreter.interpret('Aggiungi Marco Rossi')
    assert not any(str(v) in str(caught.value) for v in data.values())
    assert capsys.readouterr().out == ''


def test_name_fragment_not_accepted_as_full_name():
    interpreter, _ = interpreter_for({'operation':'add_client','first_name':'Marco'})
    with pytest.raises(ClientResponseError):interpreter.interpret('Aggiungi Gianmarco come cliente')


def test_unicode_and_whitespace_grounding():
    interpreter, _ = interpreter_for({'operation':'add_client','first_name':'ÉLÈNA Maria','last_name':'De Santis'})
    result = interpreter.interpret('Registra E\u0301lèna   Maria De  Santis come cliente')
    assert result.first_name == 'ÉLÈNA Maria' and result.last_name == 'De Santis'


@pytest.mark.parametrize('error', [httpx.ConnectError,httpx.ConnectTimeout,httpx.ReadTimeout])
def test_actual_shared_client_errors_with_mock_http(monkeypatch,error):
    calls=[]
    def handler(request):
        calls.append(request)
        raise error('simulated',request=request)
    def post(url, **kwargs):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            return client.post(url,**kwargs)
    monkeypatch.setattr(httpx,'post',post)
    with pytest.raises(OllamaError) as caught:ClientInterpreter().interpret('Aggiungi Marco Rossi')
    assert caught.value.kind is (OllamaErrorKind.CONNECTION if error is httpx.ConnectError else OllamaErrorKind.TIMEOUT)
    assert len(calls) == 1
    assert calls[0].extensions['timeout']['connect'] == 5.0
    assert calls[0].extensions['timeout']['read'] == 60.0


def test_actual_shared_client_success_with_mock_http(monkeypatch):
    calls=[]
    def handler(request):
        calls.append(request)
        payload=json.loads(request.content)
        assert payload['model']=='qwen2.5:3b' and payload['stream'] is False
        assert payload['options']=={'num_predict':256}
        return httpx.Response(200,json={'done':True,'message':{'content':json.dumps({
            'operation':'add_client','first_name':'Marco','last_name':'Rossi'})}})
    def post(url,**kwargs):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            return client.post(url,**kwargs)
    monkeypatch.setattr(httpx,'post',post)
    assert ClientInterpreter().interpret('Aggiungi Marco Rossi').missing_fields == ()
    assert len(calls) == 1


def test_interpreter_imports_without_persistence_or_calendar():
    code = '''
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in ('psycopg','client_repository','client_service','googleapiclient','telegram'):
        raise AssertionError('Forbidden dependency')
    return original(name,*args,**kwargs)
builtins.__import__ = guarded
from ai.client_interpreter import ClientInterpreter
from ai.client_schemas import ClientIntent
'''
    subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,check=True,timeout=5,capture_output=True)
