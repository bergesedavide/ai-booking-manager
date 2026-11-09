import sys
from pathlib import Path
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest
from googleapiclient.errors import HttpError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'telegram-bot'))
from reconciliation_service import Operation, inspect_operation, reconcile_one
from runtime_guard import runtime_guard

NOW = datetime.now(timezone.utc)
OP = Operation(1, 'UNKNOWN', 'a'*32, 'test-calendar', 'owner', 2, NOW, NOW)
EVENT = {'id': OP.google_event_id, 'status': 'confirmed',
         'start': {'dateTime': '2026-11-10T10:00:00+01:00'},
         'end': {'dateTime': '2026-11-10T11:00:00+01:00'}}

class ReadOnlyGoogle:
    def __init__(self, event=EVENT, error=None):
        self.event, self.error, self.calls = event, error, []
    def events(self):
        return self
    def get(self, **kwargs):
        self.calls.append(kwargs)
        return self
    def execute(self, num_retries):
        assert num_retries == 0
        if self.error:
            raise self.error
        return self.event
    def __getattr__(self, name):
        raise AssertionError('Google method forbidden: ' + name)

@pytest.mark.parametrize('state', ['PENDING', 'UNKNOWN'])
def test_positive(state):
    service = ReadOnlyGoogle()
    assert inspect_operation(replace(OP, status=state), lambda: service, 'test-calendar', 'owner') == 'VERIFIED'
    assert service.calls == [{'calendarId':'test-calendar', 'eventId':'a'*32}]

@pytest.mark.parametrize('changes,outcome', [
    ({'execution_version':1}, 'LEGACY_REVIEW_REQUIRED'),
    ({'calendar_id':None}, 'CONTEXT_MISMATCH'),
    ({'owner_ref':'other'}, 'CONTEXT_MISMATCH'),
    ({'google_event_id':None}, 'INVALID_EVENT_ID'),
    ({'google_write_started_at':None}, 'WRITE_NOT_STARTED'),
    ({'status':'CREATED'}, 'NOT_ELIGIBLE'),
])
def test_no_google(changes, outcome):
    def forbidden():
        raise AssertionError('Must not authenticate')
    assert inspect_operation(replace(OP, **changes), forbidden, 'test-calendar','owner') == outcome

@pytest.mark.parametrize('event,outcome', [
    ({**EVENT, 'status':'cancelled'}, 'CANCELLED'),
    ({**EVENT, 'status':'tentative'}, 'UNVERIFIED_EVENT'),
    ({**EVENT, 'id':'different'}, 'INVALID_RESPONSE'),
    ({**EVENT, 'start':{}}, 'INVALID_RESPONSE'),
    ({**EVENT, 'recurrence':[]}, 'UNVERIFIED_EVENT'),
    ({}, 'INVALID_RESPONSE'),
])
def test_unverified(event, outcome):
    assert inspect_operation(OP, lambda: ReadOnlyGoogle(event), 'test-calendar','owner') == outcome

@pytest.mark.parametrize('code', [404,403,429,500,503])
def test_google_errors(code):
    error = HttpError(SimpleNamespace(status=code, reason='hidden'), b'{}')
    assert inspect_operation(OP, lambda: ReadOnlyGoogle(error=error), 'test-calendar','owner') == ('NOT_FOUND' if code==404 else 'GOOGLE_ERROR')

def test_timeout():
    assert inspect_operation(OP, lambda: ReadOnlyGoogle(error=TimeoutError()), 'test-calendar','owner') == 'GOOGLE_ERROR'

def test_primary():
    with pytest.raises(ValueError):
        inspect_operation(OP, lambda: ReadOnlyGoogle(), 'primary','owner')

@pytest.mark.parametrize('preview', [True,False])
@pytest.mark.parametrize('state', ['PENDING','UNKNOWN'])
@pytest.mark.parametrize('found', [True,False])
def test_repository(preview,state,found):
    connection=MagicMock()
    cursor=connection.cursor.return_value.__enter__.return_value
    operation=replace(OP,status=state)
    cursor.fetchone.side_effect=[tuple(operation.__dict__.values()), (1,)]
    outcome=reconcile_one(connection,1,lambda: ReadOnlyGoogle(EVENT if found else {}),'test-calendar','owner',preview)
    assert outcome == ('VERIFIED' if found else 'INVALID_RESPONSE')
    assert cursor.execute.call_count == (1 if preview else 2)
    if not preview:
        sql, params=cursor.execute.call_args.args
        assert params[0] == ('CREATED' if found else state)
        assert "status IN ('PENDING','UNKNOWN')" in sql
        assert 'updated_at=%s' in sql

def test_db_failure_prevents_google():
    connection=MagicMock()
    connection.cursor.return_value.__enter__.return_value.execute.side_effect=RuntimeError()
    factory=MagicMock()
    with pytest.raises(RuntimeError):
        reconcile_one(connection,1,factory,'test-calendar','owner')
    factory.assert_not_called()

def test_conditional_update_rejected():
    connection=MagicMock()
    connection.cursor.return_value.__enter__.return_value.fetchone.side_effect=[tuple(OP.__dict__.values()),None]
    with pytest.raises(RuntimeError):
        reconcile_one(connection,1,lambda:ReadOnlyGoogle(),'test-calendar','owner')

def test_lock(tmp_path):
    with patch('runtime_guard.LOCK_PATH',tmp_path/'runtime.lock'):
        with runtime_guard():
            with pytest.raises(RuntimeError):
                with runtime_guard():
                    pytest.fail('lock bypassed')
        with runtime_guard():
            pass

def test_commit_failure_not_reported_as_success(capsys):
    import reconcile_booking as cli
    connection=MagicMock()
    connection.__exit__.side_effect=RuntimeError('secret')
    with patch.object(sys,'argv',['reconcile_booking.py','1']), patch.object(cli,'runtime_guard'), patch.object(cli,'get_connection',return_value=connection), patch.object(cli,'reconcile_one',return_value='VERIFIED'):
        assert cli.main()==1
    output=capsys.readouterr().out
    assert 'ESITO:' not in output and 'secret' not in output


def test_marker_failure_blocks_google_insert():
    import booking_service
    service=MagicMock()
    with patch.object(booking_service,'authenticate'), patch.object(booking_service,'build',return_value=service), patch.object(booking_service,'create_booking') as create:
        def fail():
            raise RuntimeError('DB commit failed')
        with pytest.raises(RuntimeError):
            booking_service.process_booking('test-calendar','Test',NOW,60,'a'*32,True,before_google_write=fail)
        create.assert_not_called()


def test_old_bot_detected(tmp_path):
    import runtime_guard as guard
    process=MagicMock()
    process.name='999999'
    (process.__truediv__.return_value).read_bytes.return_value=b'/usr/bin/python3\0/path/bot.py\0'
    with patch.object(guard.Path,'iterdir',return_value=iter([process])), patch.object(guard,'LOCK_PATH',tmp_path/'lock'):
        with pytest.raises(RuntimeError,match='Arrestare'):
            with guard.runtime_guard():
                pytest.fail('old bot bypassed')


def test_update_failure_propagates():
    connection=MagicMock()
    cursor=connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value=tuple(OP.__dict__.values())
    cursor.execute.side_effect=[None,RuntimeError('DB unavailable')]
    with pytest.raises(RuntimeError):
        reconcile_one(connection,1,lambda:ReadOnlyGoogle(),'test-calendar','owner')


def test_missing_operation_never_authenticates():
    connection=MagicMock()
    connection.cursor.return_value.__enter__.return_value.fetchone.return_value=None
    factory=MagicMock()
    assert reconcile_one(connection,123,factory,'test-calendar','owner')=='OPERATION_NOT_FOUND'
    factory.assert_not_called()
