"""Offline real Google API Client -> requests -> urllib3 -> simulated connection."""
from datetime import datetime, timedelta, timezone
import http.client
import io
import json
from pathlib import Path
import socket
import ssl
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch
import pytest
import requests
import urllib3
from googleapiclient.discovery import build_from_document
from googleapiclient.discovery_cache import get_static_doc
from googleapiclient.errors import HttpError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'google-calendar-prototype'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'telegram-bot'))
import google_transport as t

CAL='test-calendar@group.calendar.google.com'
EID='a'*32
BASE='https://www.googleapis.com/calendar/v3/calendars/test-calendar/events'


@pytest.fixture
def network(monkeypatch):
    state=SimpleNamespace(sends=[],status=200,headers={'Content-Type':'application/json'},body=json.dumps({'id':EID}).encode(),error=None,response_error=None,timeouts=[],connections=[])
    class Connection:
        is_closed=False
        is_verified=True
        proxy_is_verified=True
        proxy=None
        has_connected_to_proxy=False
        timeout=None
        def request(self,method,url,body=None,headers=None,**kwargs):
            state.sends.append((method,url,body,dict(headers or {})))
            state.timeouts.append(('connect',self.timeout))
            if state.error:raise state.error
        def getresponse(self):
            state.timeouts.append(('read',self.timeout))
            if state.response_error:raise state.response_error
            return urllib3.response.HTTPResponse(body=io.BytesIO(state.body),status=state.status,
                headers=state.headers,preload_content=False,request_method=state.sends[-1][0])
        def close(self):self.is_closed=True
    def get_connection(pool,timeout=None):
        pool.pool.get(block=False)  # Mirror the real pool checkout, without a socket.
        conn=Connection();state.connections.append(conn);return conn
    # Keep urllib3 urlopen, _make_request, retry/error handling and requests real.
    monkeypatch.setattr(urllib3.connectionpool.HTTPSConnectionPool,'_get_conn',get_connection)
    monkeypatch.setattr(socket,'create_connection',Mock(side_effect=AssertionError('Real network forbidden')))
    monkeypatch.setattr(urllib3.util.connection,'create_connection',Mock(side_effect=AssertionError('Real network forbidden')))
    return state


@pytest.fixture
def credentials():
    c=Mock(valid=True,refresh_token='fake-refresh')
    c.apply.side_effect=lambda headers:headers.update({'authorization':'Bearer fake-token'})
    return c


def service(credentials,read=None,write=None):
    http=t.CalendarHttp(read or Mock(),write or t.SingleWriteHttp(credentials))
    return build_from_document(get_static_doc('calendar','v3'),http=http)


def request(api,verb):
    if verb=='POST':return api.events().insert(calendarId=CAL,body={'id':EID,'summary':'Fittizio'})
    if verb=='PATCH':
        r=api.events().patch(calendarId=CAL,eventId=EID,body={'start':{'dateTime':'2026-11-25T16:00:00+01:00'}})
    else:r=api.events().delete(calendarId=CAL,eventId=EID)
    r.headers['If-Match']='"v1"'
    return r


@pytest.mark.parametrize('verb',['POST','PATCH','DELETE'])
def test_success_real_stack_single_send(network,credentials,verb):
    result=request(service(credentials),verb).execute(num_retries=0)
    assert len(network.sends)==1
    assert result==network.body if verb=='DELETE' else result['id']==EID
    method,path,body,headers=network.sends[0]
    assert method==verb
    if verb!='POST':assert EID in path
    assert headers['authorization']=='Bearer fake-token'
    if verb=='POST':assert json.loads(body)['id']==EID
    else:assert headers['If-Match']=='"v1"'
    assert network.timeouts==[('connect',5),('read',15)]
    credentials.refresh.assert_not_called()


def test_204_delete(network,credentials):
    network.status=204;network.body=b''
    assert request(service(credentials),'DELETE').execute(num_retries=0)==''
    assert len(network.sends)==1


@pytest.mark.parametrize('verb',['POST','PATCH','DELETE'])
@pytest.mark.parametrize('status',[401,429,500,503,301,302,303,307,308])
def test_status_no_replay_no_redirect(network,credentials,verb,status):
    network.status=status
    network.headers.update({'Location':BASE+'/other','Retry-After':'600'})
    with pytest.raises(HttpError) as error:request(service(credentials),verb).execute(num_retries=0)
    assert error.value.resp.status==status
    assert len(network.sends)==1
    credentials.refresh.assert_not_called()


@pytest.mark.parametrize('error',[ConnectionRefusedError(111,'refused'),ssl.SSLError('TLS failure'),socket.timeout('timeout')])
@pytest.mark.parametrize('verb',['POST','PATCH','DELETE'])
def test_connection_failures_never_resend(network,credentials,error,verb):
    network.error=error
    with pytest.raises(t.WriteTransportError):request(service(credentials),verb).execute(num_retries=0)
    assert len(network.sends)==1


@pytest.mark.parametrize('error',[http.client.BadStatusLine('bad'),http.client.ResponseNotReady(),http.client.RemoteDisconnected(),socket.timeout('read timeout')])
@pytest.mark.parametrize('verb',['POST','PATCH','DELETE'])
def test_response_interrupted_after_send(network,credentials,error,verb):
    network.response_error=error
    with pytest.raises(t.WriteTransportError):request(service(credentials),verb).execute(num_retries=0)
    assert len(network.sends)==1


def test_incomplete_body_after_send(network,credentials):
    network.body=b'{';network.headers['Content-Length']='500'
    with pytest.raises(t.WriteTransportError):request(service(credentials),'POST').execute(num_retries=0)
    assert len(network.sends)==1


def test_same_attempt_refuses_second_execute(network,credentials):
    r=request(service(credentials),'POST');r.execute(num_retries=0)
    with pytest.raises(t.WriteAttemptConsumed):r.execute(num_retries=0)
    assert len(network.sends)==1


def test_new_service_new_attempt(network,credentials):
    for _ in range(2):request(service(credentials),'POST').execute(num_retries=0)
    assert len(network.sends)==2


def test_before_send_refresh_failure_consumes_attempt(network,credentials):
    credentials.valid=False;credentials.refresh.side_effect=RuntimeError('sensitive fake-refresh')
    api=service(credentials);r=request(api,'POST')
    with pytest.raises(t.WriteTransportError) as error:r.execute(num_retries=0)
    assert not network.sends and 'sensitive' not in str(error.value)
    with pytest.raises(t.WriteAttemptConsumed):r.execute(num_retries=0)
    credentials.refresh.assert_called_once()


def test_preventive_refresh_once_then_401_no_replay(network,credentials):
    credentials.valid=False
    credentials.refresh.side_effect=lambda request:setattr(credentials,'valid',True)
    network.status=401
    with pytest.raises(HttpError):request(service(credentials),'POST').execute(num_retries=0)
    credentials.refresh.assert_called_once();assert len(network.sends)==1


@pytest.mark.parametrize('verb',['POST','PATCH','DELETE'])
def test_never_falls_back_to_read_http(network,credentials,verb):
    read=Mock();network.response_error=http.client.RemoteDisconnected()
    with pytest.raises(t.WriteTransportError):request(service(credentials,read=read),verb).execute(num_retries=0)
    read.request.assert_not_called();assert len(network.sends)==1


def test_freebusy_post_is_read(network,credentials):
    read=Mock();read.request.return_value=(t.httplib2.Response({'status':'200','content-type':'application/json'}),b'{"calendars":{}}')
    api=service(credentials,read=read)
    assert api.freebusy().query(body={'items':[{'id':CAL}]}).execute(num_retries=0)=={'calendars':{}}
    read.request.assert_called_once();assert not network.sends


@pytest.mark.parametrize('method,url',[
    ('POST','https://www.googleapis.com/calendar/v3/calendars/test-calendar/events/'+EID),
    ('PATCH',BASE),('PUT',BASE+'/'+EID),('POST','https://wrong.example/calendar/v3/calendars/x/events'),
    ('POST','http://www.googleapis.com/calendar/v3/calendars/x/events'),
    ('POST','https://www.googleapis.com/calendar/v3/calendars/primary/events'),
    ('DELETE','https://www.googleapis.com/calendar/v3/calendars/test-calendar'),
])
def test_unexpected_routes_refused_without_network(network,credentials,method,url):
    read=Mock();http=t.CalendarHttp(read,t.SingleWriteHttp(credentials))
    with pytest.raises(t.RejectedCalendarRequest):http.request(url,method=method)
    assert not network.sends;read.request.assert_not_called()


def test_explicit_no_retry_adapter_and_no_environment_proxies(network,credentials,monkeypatch):
    real=requests.Session
    seen=[]
    def session():
        value=real();seen.append(value);return value
    monkeypatch.setattr(t.requests,'Session',session)
    request(service(credentials),'POST').execute(num_retries=0)
    value=seen[0];assert value.trust_env is False
    retry=value.get_adapter(BASE).max_retries
    assert (retry.total,retry.connect,retry.read,retry.redirect,retry.status,retry.other)==(0,0,0,0,0,0)
    assert retry.respect_retry_after_header is False


def test_local_credentials_no_interactive_or_save(monkeypatch,credentials):
    from google_auth_oauthlib.flow import InstalledAppFlow
    loader=Mock(return_value=credentials)
    monkeypatch.setattr(t.Credentials,'from_authorized_user_file',loader)
    monkeypatch.setattr(InstalledAppFlow,'run_local_server',Mock(side_effect=AssertionError('Interactive OAuth forbidden')))
    with patch.object(Path,'write_text',side_effect=AssertionError('No token write')):
        assert t.load_existing_credentials() is credentials
    assert loader.call_args.kwargs=={}
    loader.side_effect=FileNotFoundError('sensitive')
    with pytest.raises(t.WriteTransportError) as error:t.load_existing_credentials()
    assert 'sensitive' not in str(error.value)


def test_upper_api_retry_cannot_send_twice(network,credentials):
    network.status=503
    r=request(service(credentials),'POST');r._sleep=lambda seconds:None
    with pytest.raises(t.WriteAttemptConsumed):r.execute(num_retries=1)
    assert len(network.sends)==1


def test_method_override_never_sent(network,credentials):
    with pytest.raises(t.RejectedCalendarRequest):
        t.SingleWriteHttp(credentials).request(BASE,method='POST',body='{}',headers={'x-http-method-override':'GET'})
    assert not network.sends


def test_factory_separates_read_and_write_and_does_not_pass_credentials_twice(credentials,monkeypatch):
    build=Mock(return_value=object());read=Mock()
    monkeypatch.setattr(t,'api_build',build)
    monkeypatch.setattr(t,'AuthorizedHttp',Mock(return_value=read))
    first=t.build_calendar_service('calendar','v3',credentials=credentials)
    http1=build.call_args.kwargs['http']
    assert first is build.return_value and http1.read_http is read
    assert isinstance(http1.write_http,t.SingleWriteHttp)
    assert 'credentials' not in build.call_args.kwargs and build.call_args.kwargs['num_retries']==0
    t.build_calendar_service('calendar','v3',credentials=credentials)
    assert build.call_args.kwargs['http'].write_http is not http1.write_http


@pytest.fixture
def app_service(monkeypatch,credentials):
    from test_ai_reschedule import EVENT
    read=Mock()
    def read_request(uri,**kwargs):
        assert kwargs['method']=='GET'
        return (t.httplib2.Response({'status':'200','content-type':'application/json'}),json.dumps(EVENT).encode())
    read.request.side_effect=read_request
    monkeypatch.setattr(t,'AuthorizedHttp',Mock(return_value=read))
    # Real api_build uses packaged static discovery; no discovery network.
    return t.build_calendar_service('calendar','v3',credentials=credentials),read


@pytest.mark.parametrize('status',[200,401,429,503])
def test_process_booking_routes_real_stack_and_keeps_result_semantics(network,credentials,monkeypatch,status):
    import booking_service as booking
    read=Mock()
    read.request.return_value=(t.httplib2.Response({'status':'404','content-type':'application/json'}),b'{}')
    monkeypatch.setattr(t,'AuthorizedHttp',Mock(return_value=read))
    monkeypatch.setattr(booking,'authenticate',Mock(return_value=credentials))
    monkeypatch.setattr(booking,'check_availability',Mock(return_value=True))
    network.status=status
    marker=Mock()
    result=booking.process_booking(CAL,'Test',datetime(2026,11,25,15,tzinfo=timezone.utc),30,EID,before_google_write=marker)
    assert len(network.sends)==1;marker.assert_called_once()
    assert result.status==(booking.BookingStatus.CREATED if status==200 else booking.BookingStatus.UNKNOWN)
    if status!=200:read.request.assert_called_once()  # Allowed recovery GET, no second write.


@pytest.mark.parametrize('status',[200,401,429,503])
def test_reschedule_real_stack_journal_result(network,app_service,monkeypatch,status):
    from test_ai_reschedule import PLAN,repository
    import ai_reschedule_service as move
    api,read=app_service;repo=repository()
    monkeypatch.setattr(move,'availability',Mock(return_value=move.AvailabilityStatus.AVAILABLE))
    network.status=status
    network.body=json.dumps({'id':PLAN.event_id,'summary':PLAN.title,'status':'confirmed','etag':'v2',
        'start':{'dateTime':PLAN.new_start.isoformat()},'end':{'dateTime':PLAN.new_end.isoformat()}}).encode()
    result=move.execute(api,repo,PLAN,'test-calendar','owner')
    assert len(network.sends)==1 and network.sends[0][0]=='PATCH'
    assert network.sends[0][3]['If-Match']==PLAN.etag
    expected='UPDATED' if status==200 else 'UNKNOWN'
    assert result==expected;repo.finish.assert_called_once_with(1,expected,expected)


@pytest.mark.parametrize('status',[204,401,429,503])
def test_deletion_real_stack_journal_result(network,app_service,status):
    from test_ai_deletion import PLAN,repo as repository
    import ai_deletion_service as deletion
    api,read=app_service;repo=repository();network.status=status
    if status==204:network.body=b''
    result=deletion.execute(api,repo,PLAN,'test-calendar','owner')
    assert len(network.sends)==1 and network.sends[0][0]=='DELETE'
    assert network.sends[0][3]['If-Match']==PLAN.etag
    expected='DELETED' if status==204 else 'UNKNOWN'
    assert result==expected;repo.finish.assert_called_once_with(1,expected,expected)


def test_postwrite_journal_error_no_second_dispatch(network,app_service,monkeypatch):
    from test_ai_reschedule import PLAN,repository
    import ai_reschedule_service as move
    api,_=app_service;repo=repository();repo.finish.side_effect=RuntimeError('DB unavailable')
    monkeypatch.setattr(move,'availability',Mock(return_value=move.AvailabilityStatus.AVAILABLE))
    network.status=503
    with pytest.raises(RuntimeError):move.execute(api,repo,PLAN,'test-calendar','owner')
    assert len(network.sends)==1
    repo.finish.assert_called_once_with(1,'UNKNOWN','UNKNOWN')


def test_preventive_oauth_timeout_is_bounded():
    with patch.object(t.Request,'__call__',return_value=Mock()) as call:
        t.BoundedAuthRequest()('https://oauth2.googleapis.com/token',method='POST')
    assert call.call_args.kwargs['timeout']==(5,15)


def test_session_connections_closed_after_interrupted_response(network,credentials):
    network.response_error=http.client.RemoteDisconnected()
    with pytest.raises(t.WriteTransportError):request(service(credentials),'POST').execute(num_retries=0)
    assert network.connections and all(conn.is_closed for conn in network.connections)


def test_competing_calls_on_one_attempt_have_one_dispatch(network,credentials):
    from concurrent.futures import ThreadPoolExecutor
    http=t.SingleWriteHttp(credentials)
    def send():
        try:
            http.request(BASE,method='POST',body='{}')
            return 'sent'
        except t.WriteAttemptConsumed:
            return 'blocked'
    with ThreadPoolExecutor(max_workers=2) as executor:
        results=list(executor.map(lambda _:send(),range(2)))
    assert sorted(results)==['blocked','sent'] and len(network.sends)==1
