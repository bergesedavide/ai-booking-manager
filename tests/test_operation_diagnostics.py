"""Offline diagnostics: fake SQL connection and read-only Google client."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'telegram-bot'))
import operation_diagnostics as d
import diagnose_operations as cli
import runtime_guard as guard

NOW = datetime(2026,11,9,12,tzinfo=timezone.utc)
CAL = 'test-calendar'
EID = 'a'*32
SECRET = 'private-token-do-not-print'


def snapshot(kind='creation',**changes):
    origin=dict(id=1,request_key='ai:2:3',status='CREATED',google_event_id=EID,calendar_id=CAL,
                owner_ref=d.OWNER_REF,google_write_started_at=NOW,created_at=NOW,updated_at=NOW,
                execution_version=2,last_reconciled_at=None,last_reconciliation_outcome=None)
    data=dict(origin,status='UNKNOWN')
    if kind!='creation':
        data={k:v for k,v in data.items() if k not in ('execution_version','last_reconciled_at','last_reconciliation_outcome')}
        data.update(booking_operation_id=1,expected_etag='v1',original_start=NOW,original_end=NOW+timedelta(minutes=30),outcome='UNKNOWN')
        if kind=='reschedule':
            data.update(requested_start=NOW+timedelta(days=1),requested_end=NOW+timedelta(days=1,minutes=30))
    data.update(changes)
    return d.Snapshot(kind,data,origin if kind!='creation' else None)


class FakeDB:
    def __init__(self,snap=None,listing=False):
        self.snap=snap;self.listing=listing;self.active=False;self.queries=[];self.result=None
    @contextmanager
    def connect(self,password):
        assert not self.active
        self.active=True
        try:yield self
        finally:self.active=False
    def cursor(self):return self
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def execute(self,sql,params=None):
        assert self.active
        normalized=sql.upper()
        assert not any(word in normalized.split() for word in ('INSERT','UPDATE','DELETE','CREATE','ALTER','DROP'))
        assert 'FOR UPDATE' not in normalized
        self.queries.append((sql,params))
        if normalized.startswith('SET TRANSACTION'):
            assert 'READ ONLY' in normalized
            return
        assert normalized.startswith('SELECT')
        # Every new connection must begin with READ ONLY, never a row-lock query.
        assert any('READ ONLY' in query for query,_ in self.queries)
        if 'UNION ALL' in sql:
            self.result=[('creation',1,'PENDING',60,False),('reschedule',1,'UNKNOWN',30,True),('deletion',1,'UNKNOWN',10,True)]
        else:
            fields=sql.split('SELECT ',1)[1].split(' FROM ',1)[0].split(',')
            record=self.snap.creation if ('booking_operations' in sql and self.snap.kind!='creation') else self.snap.data
            self.result=tuple(record.get(field) for field in fields) if record is not None else None
    def fetchone(self):return self.result
    def fetchall(self):return self.result


class ReadOnlyGoogle:
    def __init__(self,event=None,http=None,calendar=None,error=None):
        self.event=event if event is not None else {'id':EID,'status':'confirmed','etag':'v2',
            'start':{'dateTime':NOW.isoformat()},'end':{'dateTime':(NOW+timedelta(minutes=30)).isoformat()}}
        self.http=http;self.calendar=calendar if calendar is not None else {'id':CAL,'summary':d.EXPECTED_CALENDAR}
        self.error=error;self.calls=[]
    def calendarList(self):self.stage='calendar';return self
    def events(self):self.stage='event';return self
    def get(self,**kwargs):self.calls.append((self.stage,kwargs));return self
    def execute(self,num_retries):
        assert num_retries==0
        if self.error:raise self.error
        if self.http:
            from googleapiclient.errors import HttpError
            raise HttpError(SimpleNamespace(status=self.http,reason=SECRET),SECRET.encode())
        return self.calendar if self.stage=='calendar' else self.event
    def __getattr__(self,name):raise AssertionError('Forbidden Google method: '+name)


def test_list_all_three_and_coincident_ids(monkeypatch):
    db=FakeDB(listing=True);monkeypatch.setattr(d,'get_connection',db.connect)
    rows=d.list_unresolved('unused',12)
    assert [r['kind'] for r in rows]==list(d.TABLES)
    assert [r['id'] for r in rows]==[1,1,1]
    assert rows[0]['write_started'] is False
    assert 'ORDER BY created_at,kind,id LIMIT %s' in db.queries[1][0]
    assert db.queries[1][1]==(12,) and not db.active


@pytest.mark.parametrize('kind',['creation','reschedule','deletion'])
def test_show_reads_correct_table_and_origin_without_google(monkeypatch,kind):
    snap=snapshot(kind);db=FakeDB(snap);monkeypatch.setattr(d,'get_connection',db.connect)
    factory=Mock(side_effect=AssertionError('No network allowed'))
    report=d.diagnose('unused',kind,1,CAL,check_google=False,service_factory=factory)
    assert report['postgresql']['type']==kind
    assert report['postgresql']['record']==snap.data
    assert report['google']['code']=='NOT_REQUESTED'
    factory.assert_not_called()
    assert all('FOR UPDATE' not in sql for sql,_ in db.queries)
    assert 'READ ONLY' in db.queries[0][0] and not db.active


@pytest.mark.parametrize('kind',['creation','reschedule','deletion'])
def test_releases_transactions_before_google_and_rereads(monkeypatch,kind):
    db=FakeDB(snapshot(kind));monkeypatch.setattr(d,'get_connection',db.connect)
    def factory():
        assert not db.active
        return ReadOnlyGoogle()
    report=d.diagnose('unused',kind,1,CAL,check_google=True,service_factory=factory)
    assert report['google']['code']=='ACTIVE' and not report['record_changed']
    assert sum('READ ONLY' in sql for sql,_ in db.queries)==2
    assert not db.active


@pytest.mark.parametrize('change,code',[
    ({'execution_version':1},'LEGACY_OR_INSUFFICIENT_ORIGIN'),
    ({'calendar_id':'other'},'CONTEXT_MISMATCH'),
    ({'owner_ref':'other'},'CONTEXT_MISMATCH'),
    ({'google_event_id':'invalid!'},'INVALID_EVENT_ID'),
])
def test_invalid_context_no_authentication(change,code):
    factory=Mock()
    assert d.probe_google(snapshot(**change),CAL,d.OWNER_REF,factory)['code']==code
    factory.assert_not_called()


@pytest.mark.parametrize('calendar',['primary',' PRIMARY ',''])
def test_primary_or_missing_calendar_refused(calendar):
    factory=Mock()
    assert d.probe_google(snapshot(),calendar,d.OWNER_REF,factory)['code']=='CONTEXT_MISMATCH'
    factory.assert_not_called()


def test_child_missing_or_incoherent_origin():
    factory=Mock()
    for snap in (replace(snapshot('deletion'),creation=None),snapshot('deletion',booking_operation_id=9),
                 snapshot('reschedule',expected_etag='')):
        assert d.probe_google(snap,CAL,d.OWNER_REF,factory)['stage']=='local_validation'
    factory.assert_not_called()


@pytest.mark.parametrize('calendar',[{'id':'other','summary':d.EXPECTED_CALENDAR},
    {'id':CAL,'summary':'Other calendar'},{'id':CAL,'summary':d.EXPECTED_CALENDAR,'primary':True}])
def test_google_calendar_checked_before_event(calendar):
    service=ReadOnlyGoogle(calendar=calendar)
    assert d.probe_google(snapshot(),CAL,d.OWNER_REF,lambda:service)['code']=='CALENDAR_MISMATCH'
    assert [stage for stage,_ in service.calls]==['calendar']


@pytest.mark.parametrize('event,code',[
    ({'id':EID,'status':'cancelled'},'CANCELLED'),
    ({'id':'other','status':'confirmed'},'UNEXPECTED_EVENT_ID'),
    ({},'INCOMPLETE_RESPONSE'),
    ({'id':EID,'status':'confirmed','etag':'v1'},'INCOMPLETE_RESPONSE'),
    ({'id':EID,'status':'confirmed','etag':'v1','start':{'dateTime':'2026-11-09T12:00:00'},'end':{'dateTime':'2026-11-09T13:00:00'}},'INCOMPLETE_RESPONSE'),
])
def test_event_observations(event,code):
    service=ReadOnlyGoogle(event=event)
    observed=d.probe_google(snapshot(),CAL,d.OWNER_REF,lambda:service)
    assert observed['code']==code
    assert service.calls[-1][1]['eventId']==EID


@pytest.mark.parametrize('http,code',[(403,'FORBIDDEN'),(404,'NOT_FOUND_404'),(410,'GONE_410'),(500,'GOOGLE_ERROR')])
def test_http_errors_no_details(http,code):
    observation=d.probe_google(snapshot(),CAL,d.OWNER_REF,lambda:ReadOnlyGoogle(http=http))
    assert observation['code']==code and SECRET not in json.dumps(observation)


def test_timeout_and_auth_error_sanitized():
    observed=d.probe_google(snapshot(),CAL,d.OWNER_REF,lambda:ReadOnlyGoogle(error=TimeoutError(SECRET)))
    assert observed['code']=='TIMEOUT'
    assert SECRET not in json.dumps(observed)
    factory=Mock(side_effect=d.AuthenticationRefused(SECRET))
    assert d.probe_google(snapshot(),CAL,d.OWNER_REF,factory)['code']=='AUTHENTICATION_UNAVAILABLE'


@pytest.mark.parametrize('kind',['reschedule','deletion'])
def test_desired_state_is_not_attributed_to_attempt(kind):
    snap=snapshot(kind)
    service=ReadOnlyGoogle(event={'id':EID,'status':'cancelled'}) if kind=='deletion' else ReadOnlyGoogle(event={
        'id':EID,'status':'confirmed','etag':'v2','start':{'dateTime':snap.data['requested_start'].isoformat()},
        'end':{'dateTime':snap.data['requested_end'].isoformat()}})
    observation=d.probe_google(snap,CAL,d.OWNER_REF,lambda:service)
    assert d.INSUFFICIENT in d.conclusion(snap,observation)


def test_marker_and_creation_evidence():
    assert 'non ha avviato' in d.conclusion(snapshot(google_write_started_at=None))
    assert d.INSUFFICIENT in d.conclusion(snapshot(execution_version=1,google_write_started_at=None))
    observation=d.probe_google(snapshot(),CAL,d.OWNER_REF,lambda:ReadOnlyGoogle())
    assert 'Creazione riscontrata' in d.conclusion(snapshot(),observation)
    for code in ('NOT_FOUND_404','GONE_410'):
        assert d.INSUFFICIENT in d.conclusion(snapshot('deletion'),{'code':code})


def test_changed_record_blocks_conclusion(monkeypatch):
    before=snapshot();after=snapshot(status='CREATED')
    reader=Mock(side_effect=[before,after]);monkeypatch.setattr(d,'read_snapshot',reader)
    report=d.diagnose('unused','creation',1,CAL,check_google=True,service_factory=lambda:ReadOnlyGoogle())
    assert report['record_changed'] and d.INSUFFICIENT in report['conclusion']


def test_reread_db_failure_retains_observation_without_success(monkeypatch):
    monkeypatch.setattr(d,'read_snapshot',Mock(side_effect=[snapshot(),RuntimeError(SECRET)]))
    report=d.diagnose('unused','creation',1,CAL,check_google=True,service_factory=lambda:ReadOnlyGoogle())
    assert report['google']['code']=='ACTIVE' and report['reread_error']=='POSTGRESQL_UNAVAILABLE'
    assert d.INSUFFICIENT in report['conclusion'] and SECRET not in str(report)


@pytest.fixture
def local_auth(monkeypatch,tmp_path):
    from google.oauth2.credentials import Credentials
    from googleapiclient import discovery
    token=tmp_path/'token.json';client=tmp_path/'credentials.json'
    token.write_text('existing token bytes');client.write_text(json.dumps({'installed':{'client_id':'local-client'}}))
    credentials=Mock(client_id='local-client',valid=True,expired=False,refresh_token=SECRET,
        scopes=['https://www.googleapis.com/auth/calendar.calendarlist.readonly','https://www.googleapis.com/auth/calendar.events.owned'])
    loader=Mock(return_value=credentials);builder=Mock(return_value=ReadOnlyGoogle())
    monkeypatch.setattr(Credentials,'from_authorized_user_file',loader)
    monkeypatch.setattr(discovery,'build',builder)
    return credentials,loader,builder,token,client


@pytest.mark.parametrize('refresh',[False,True])
def test_authentication_never_writes_token_or_requests_scopes(local_auth,refresh):
    credentials,loader,builder,token,client=local_auth
    if refresh:
        credentials.valid=False;credentials.expired=True
        credentials.refresh.side_effect=lambda request:setattr(credentials,'valid',True)
    before=(token.read_bytes(),client.read_bytes(),token.stat().st_mtime_ns,client.stat().st_mtime_ns)
    with patch.object(Path,'write_text',side_effect=AssertionError('No token write')):
        assert d.local_google_service(token,client) is builder.return_value
    assert before==(token.read_bytes(),client.read_bytes(),token.stat().st_mtime_ns,client.stat().st_mtime_ns)
    loader.assert_called_once_with(str(token))
    assert builder.call_args.kwargs['cache_discovery'] is False
    if refresh:credentials.refresh.assert_called_once()
    else:credentials.refresh.assert_not_called()


@pytest.mark.parametrize('condition',['scopes','client','refresh_missing','token_missing'])
def test_authentication_refuses_unsafe_setup(local_auth,condition):
    credentials,loader,builder,token,client=local_auth
    if condition=='scopes':credentials.scopes=[]
    if condition=='client':credentials.client_id='other'
    if condition=='refresh_missing':credentials.valid=False;credentials.expired=True;credentials.refresh_token=None
    if condition=='token_missing':loader.side_effect=FileNotFoundError(SECRET)
    with pytest.raises(d.AuthenticationRefused):d.local_google_service(token,client)
    builder.assert_not_called()


@contextmanager
def unlocked():yield


@pytest.fixture
def cli_config(monkeypatch):
    monkeypatch.setattr(cli,'runtime_guard',unlocked)
    monkeypatch.setattr(cli,'load_config',lambda:{'POSTGRES_PASSWORD':SECRET,'GOOGLE_TEST_CALENDAR_ID':CAL})


def test_cli_show_does_not_request_google(cli_config,monkeypatch,capsys):
    diagnose=Mock(return_value={'google':{'code':'NOT_REQUESTED'}});monkeypatch.setattr(cli,'diagnose',diagnose)
    assert cli.main(['show','creation','1'])==0
    assert diagnose.call_args.args[-1] is False
    assert SECRET not in capsys.readouterr().out


def test_cli_optin_and_list_limit(cli_config,monkeypatch):
    diagnose=Mock(return_value={});listing=Mock(return_value=[])
    monkeypatch.setattr(cli,'diagnose',diagnose);monkeypatch.setattr(cli,'list_unresolved',listing)
    assert cli.main(['show','deletion','1','--check-google'])==0
    assert diagnose.call_args.args[-1] is True
    assert cli.main(['list','--limit','7'])==0
    listing.assert_called_once_with(SECRET,7)


def test_cli_lock_busy_no_db_or_auth(cli_config,monkeypatch,capsys):
    @contextmanager
    def busy():raise RuntimeError(SECRET);yield
    monkeypatch.setattr(cli,'runtime_guard',busy)
    listing=Mock();monkeypatch.setattr(cli,'list_unresolved',listing)
    assert cli.main(['list'])==1
    listing.assert_not_called();assert SECRET not in capsys.readouterr().out


def test_real_runtime_lock_contention(monkeypatch,tmp_path):
    monkeypatch.setattr(guard,'LOCK_PATH',tmp_path/'lock')
    with guard.runtime_guard():
        with pytest.raises(RuntimeError):
            with guard.runtime_guard():pytest.fail('Lock bypass')
    with guard.runtime_guard():pass


def test_bot_process_refused(monkeypatch,tmp_path):
    process=MagicMock();process.name='999999'
    (process/'cmdline').read_bytes.return_value=b'/usr/bin/python3\0/path/bot.py\0'
    monkeypatch.setattr(guard.Path,'iterdir',lambda self:iter([process]))
    monkeypatch.setattr(guard,'LOCK_PATH',tmp_path/'lock')
    with pytest.raises(RuntimeError):
        with guard.runtime_guard():pytest.fail('Bot guard bypass')


def test_initial_db_failure_no_google_or_sensitive_output(cli_config,monkeypatch,capsys):
    monkeypatch.setattr(d,'get_connection',Mock(side_effect=RuntimeError(SECRET)))
    factory=Mock();monkeypatch.setattr(d,'local_google_service',factory)
    assert cli.main(['show','creation','1','--check-google'])==1
    factory.assert_not_called();assert SECRET not in capsys.readouterr().out


@pytest.mark.parametrize('args',[['show','invalid','1'],['show','creation','0'],['list','--limit','201']])
def test_bad_cli_arguments_do_not_connect(monkeypatch,args):
    loader=Mock();monkeypatch.setattr(cli,'load_config',loader)
    with pytest.raises(SystemExit):cli.main(args)
    loader.assert_not_called()


@pytest.mark.parametrize('snap',[
    snapshot('reschedule',status='UPDATED',outcome='UNKNOWN'),
    snapshot('deletion',status='DELETED',outcome='UNKNOWN'),
    snapshot('creation',status='CREATED',google_write_started_at=None),
    snapshot('creation',google_write_started_at='bad timestamp'),
    snapshot('creation',request_key=''),
])
def test_incoherent_record_never_authenticates(snap):
    factory=Mock()
    assert d.probe_google(snap,CAL,d.OWNER_REF,factory)['code']=='INVALID_RECORD'
    factory.assert_not_called()


def test_observation_does_not_dump_extra_event_payload():
    service=ReadOnlyGoogle()
    service.event['summary']=SECRET
    service.event['description']=SECRET
    service.event['start']['private_field']=SECRET
    observation=d.probe_google(snapshot(),CAL,d.OWNER_REF,lambda:service)
    assert observation['code']=='ACTIVE' and SECRET not in str(observation)


def test_missing_record_and_postcheck_disappearance(monkeypatch):
    monkeypatch.setattr(d,'read_snapshot',Mock(return_value=None))
    factory=Mock()
    assert d.diagnose('unused','creation',1,CAL,check_google=True,service_factory=factory)=={'error':'OPERATION_NOT_FOUND'}
    factory.assert_not_called()
    monkeypatch.setattr(d,'read_snapshot',Mock(side_effect=[snapshot(),None]))
    result=d.diagnose('unused','creation',1,CAL,check_google=True,service_factory=lambda:ReadOnlyGoogle())
    assert result['record_changed'] and d.INSUFFICIENT in result['conclusion']


def test_refresh_failure_no_sensitive_details(local_auth):
    credentials,loader,builder,token,client=local_auth
    credentials.valid=False;credentials.expired=True
    credentials.refresh.side_effect=RuntimeError(SECRET)
    before=token.read_bytes()
    with pytest.raises(d.AuthenticationRefused) as error:d.local_google_service(token,client)
    assert SECRET not in str(error.value) and token.read_bytes()==before
    builder.assert_not_called()


def test_lock_released_even_if_diagnostics_fail(monkeypatch,tmp_path,capsys):
    monkeypatch.setattr(guard,'LOCK_PATH',tmp_path/'lock')
    monkeypatch.setattr(cli,'load_config',Mock(side_effect=RuntimeError(SECRET)))
    assert cli.main(['list'])==1
    with guard.runtime_guard():pass
    assert SECRET not in capsys.readouterr().out


@pytest.mark.parametrize('status,expected',[(403,'FORBIDDEN'),(404,'NOT_FOUND_404'),(410,'GONE_410')])
def test_event_http_errors_after_successful_calendar_check(status,expected):
    class EventError(ReadOnlyGoogle):
        def execute(self,num_retries):
            if self.stage=='event':self.http=status
            return super().execute(num_retries)
    service=EventError()
    observation=d.probe_google(snapshot('deletion'),CAL,d.OWNER_REF,lambda:service)
    assert observation['stage']=='event' and observation['code']==expected
    assert d.INSUFFICIENT in d.conclusion(snapshot('deletion'),observation)
    assert [stage for stage,_ in service.calls]==['calendar','event']


def test_changed_origin_is_reported_even_if_selected_row_unchanged(monkeypatch):
    before=snapshot('reschedule')
    after=replace(before,creation=dict(before.creation,updated_at=NOW+timedelta(seconds=1)))
    monkeypatch.setattr(d,'read_snapshot',Mock(side_effect=[before,after]))
    report=d.diagnose('unused','reschedule',1,CAL,check_google=True,service_factory=lambda:ReadOnlyGoogle())
    assert report['record_changed'] and d.INSUFFICIENT in report['conclusion']


@pytest.mark.parametrize('kind',['creation','reschedule','deletion'])
def test_invalid_selection_before_db(monkeypatch,kind):
    connection=Mock();monkeypatch.setattr(d,'get_connection',connection)
    with pytest.raises(ValueError):d.read_snapshot('unused',kind,0)
    connection.assert_not_called()
