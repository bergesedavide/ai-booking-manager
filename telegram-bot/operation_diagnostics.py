"""Read-only operation snapshots and evidence. Never reconciles or writes states."""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path

from database import get_connection
from runtime_guard import OWNER_REF

TABLES = {'creation':'booking_operations', 'reschedule':'booking_reschedules', 'deletion':'booking_deletions'}
COMMON = 'id,request_key,status,google_event_id,calendar_id,owner_ref,google_write_started_at,created_at,updated_at'
COLUMNS = {
    'creation': COMMON+',execution_version,last_reconciled_at,last_reconciliation_outcome',
    'reschedule': COMMON+',booking_operation_id,expected_etag,original_start,original_end,requested_start,requested_end,outcome',
    'deletion': COMMON+',booking_operation_id,expected_etag,original_start,original_end,outcome',
}
INSUFFICIENT = 'Evidenza insufficiente: operazione ancora incerta.'
EXPECTED_CALENDAR = 'AI Booking Manager - Test'


@dataclass(frozen=True)
class Snapshot:
    kind: str
    data: dict
    creation: dict | None = None


def _row(cursor, columns):
    row = cursor.fetchone()
    return dict(zip(columns.split(','), row)) if row is not None else None


def list_unresolved(password, limit=50):
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError('Limit must be between 1 and 200')
    parts = [f"SELECT '{kind}' AS kind,id,status,created_at,google_write_started_at "
             f"FROM public.{table} WHERE status IN ('PENDING','UNKNOWN')" for kind,table in TABLES.items()]
    query = ("SELECT kind,id,status,GREATEST(0,EXTRACT(EPOCH FROM CURRENT_TIMESTAMP-created_at)) AS age_seconds,"
             "google_write_started_at IS NOT NULL AS write_started FROM ("+' UNION ALL '.join(parts)+
             ") AS unresolved ORDER BY created_at,kind,id LIMIT %s")
    with get_connection(password) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION READ ONLY')
            cursor.execute(query, (limit,))
            return [dict(zip(('kind','id','status','age_seconds','write_started'),row)) for row in cursor.fetchall()]


def read_snapshot(password, kind, operation_id):
    if kind not in TABLES or type(operation_id) is not int or operation_id <= 0:
        raise ValueError('Invalid selection')
    with get_connection(password) as connection:
        with connection.cursor() as cursor:
            # Both rows share a short consistent snapshot. Close it before network I/O.
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            columns = COLUMNS[kind]
            cursor.execute(f'SELECT {columns} FROM public.{TABLES[kind]} WHERE id=%s', (operation_id,))
            data = _row(cursor,columns)
            if data is None:
                return None
            origin = None
            if kind != 'creation':
                columns = COLUMNS['creation']
                cursor.execute(f'SELECT {columns} FROM public.booking_operations WHERE id=%s', (data['booking_operation_id'],))
                origin = _row(cursor,columns)
            return Snapshot(kind,data,origin)


def _instant(value):
    if not isinstance(value,datetime) or value.utcoffset() is None:
        raise ValueError('Timezone required')
    return value.astimezone(timezone.utc)


def context_problem(snapshot, calendar_id, owner_ref):
    if not calendar_id or calendar_id.strip().lower() == 'primary' or owner_ref != OWNER_REF:
        return 'CONTEXT_MISMATCH'
    data = snapshot.data
    origin = data if snapshot.kind == 'creation' else snapshot.creation
    if not origin or origin.get('execution_version') != 2:
        return 'LEGACY_OR_INSUFFICIENT_ORIGIN'
    if snapshot.kind not in TABLES:
        return 'INVALID_RECORD'
    allowed = {'creation':('PENDING','UNKNOWN','CREATED','FAILED'),
               'reschedule':('PENDING','UNKNOWN','UPDATED','FAILED'),
               'deletion':('PENDING','UNKNOWN','DELETED','FAILED')}
    if data.get('status') not in allowed[snapshot.kind]:
        return 'INVALID_RECORD'
    for record in (data,origin):
        if record.get('calendar_id') != calendar_id or record.get('owner_ref') != owner_ref:
            return 'CONTEXT_MISMATCH'
    if not isinstance(data.get('request_key'),str) or not data['request_key'].strip():
        return 'INVALID_RECORD'
    for record in (data,origin):
        try:
            if record.get('google_write_started_at') is not None:
                _instant(record['google_write_started_at'])
        except (TypeError,ValueError):
            return 'INVALID_RECORD'
    if data['status'] in ('CREATED','UPDATED','DELETED') and not data.get('google_write_started_at'):
        return 'INVALID_RECORD'
    event_id = data.get('google_event_id')
    if not isinstance(event_id,str) or not 5 <= len(event_id) <= 1024 or any(c not in '0123456789abcdefghijklmnopqrstuv' for c in event_id):
        return 'INVALID_EVENT_ID'
    if origin.get('google_event_id') != event_id:
        return 'ORIGIN_MISMATCH'
    if snapshot.kind != 'creation':
        expected_outcome = {'PENDING':None,'UNKNOWN':'UNKNOWN','UPDATED':'UPDATED','DELETED':'DELETED','FAILED':'STALE'}
        if data.get('outcome') != expected_outcome[data['status']]:
            return 'INVALID_RECORD'
        if data.get('booking_operation_id') != origin.get('id') or origin.get('status') != 'CREATED' or not origin.get('google_write_started_at'):
            return 'ORIGIN_MISMATCH'
        try:
            start,end = _instant(data['original_start']),_instant(data['original_end'])
            if end <= start or not isinstance(data.get('expected_etag'),str) or not data['expected_etag'].strip():
                return 'INVALID_RECORD'
            if snapshot.kind == 'reschedule':
                new_start,new_end = _instant(data['requested_start']),_instant(data['requested_end'])
                if new_end <= new_start or new_end-new_start != end-start:
                    return 'INVALID_RECORD'
        except (KeyError,TypeError,ValueError):
            return 'INVALID_RECORD'
    return None


class AuthenticationRefused(Exception):
    pass


def local_google_service(token_path=None, client_path=None):
    """Existing local credentials only. Refresh in memory; no OAuth flow or writes."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.discovery import build
    import httplib2
    base = Path(__file__).resolve().parents[1]/'google-calendar-prototype'
    token_path = Path(token_path) if token_path is not None else base/'token.json'
    client_path = Path(client_path) if client_path is not None else base/'credentials.json'
    try:
        credentials = Credentials.from_authorized_user_file(str(token_path))  # No scopes requested.
        app = json.loads(client_path.read_text())
        client = app.get('installed',app.get('web',{}))
        if not credentials.client_id or credentials.client_id != client.get('client_id'):
            raise AuthenticationRefused()
        scopes = set(credentials.scopes or ())
        prefix = 'https://www.googleapis.com/auth/'
        calendar_access = bool(scopes & {prefix+'calendar',prefix+'calendar.readonly'})
        can_list = calendar_access or bool(scopes & {prefix+'calendar.calendarlist',prefix+'calendar.calendarlist.readonly'})
        can_read = calendar_access or bool(scopes & {prefix+'calendar.events',prefix+'calendar.events.readonly',prefix+'calendar.events.owned',prefix+'calendar.events.owned.readonly'})
        if not can_list or not can_read:
            raise AuthenticationRefused()
        if not credentials.valid:
            if not credentials.expired or not credentials.refresh_token:
                raise AuthenticationRefused()
            class BoundedRequest(Request):
                def __call__(self,*args,**kwargs):
                    kwargs['timeout'] = 10
                    return super().__call__(*args,**kwargs)
            credentials.refresh(BoundedRequest())
        if not credentials.valid:
            raise AuthenticationRefused()
        return build('calendar','v3',http=AuthorizedHttp(credentials,http=httplib2.Http(timeout=10)),cache_discovery=False)
    except Exception:
        raise AuthenticationRefused() from None


def probe_google(snapshot, calendar_id, owner_ref, service_factory=local_google_service):
    problem = context_problem(snapshot,calendar_id,owner_ref)
    if problem:
        return {'code':problem,'stage':'local_validation'}
    stage = 'authentication'
    try:
        service = service_factory()
        stage = 'calendar'
        calendar = service.calendarList().get(calendarId=calendar_id,fields='id,summary,primary').execute(num_retries=0)
        if not isinstance(calendar,dict) or calendar.get('id') != calendar_id or calendar.get('summary') != EXPECTED_CALENDAR or calendar.get('primary',False) is not False:
            return {'code':'CALENDAR_MISMATCH','stage':stage}
        stage = 'event'
        event_id = snapshot.data['google_event_id']
        event = service.events().get(calendarId=calendar_id,eventId=event_id,
            fields='id,status,etag,start,end').execute(num_retries=0)
        if not isinstance(event,dict):
            return {'code':'INCOMPLETE_RESPONSE','stage':stage}
        if event.get('id') != event_id:
            return {'code':'UNEXPECTED_EVENT_ID' if event.get('id') else 'INCOMPLETE_RESPONSE','stage':stage}
        evidence = {key:event[key] for key in ('id','status','etag') if key in event}
        for key in ('start','end'):
            if isinstance(event.get(key),dict):
                evidence[key] = {field:value for field,value in event[key].items()
                                 if field in ('dateTime','timeZone','date') and isinstance(value,str)}
        if event.get('status') == 'cancelled':
            return {'code':'CANCELLED','stage':stage,'event':evidence}
        if event.get('status') not in ('confirmed','tentative') or not event.get('etag'):
            return {'code':'INCOMPLETE_RESPONSE','stage':stage,'event':evidence}
        try:
            start = _instant(datetime.fromisoformat(event['start']['dateTime']))
            end = _instant(datetime.fromisoformat(event['end']['dateTime']))
            if end <= start:
                raise ValueError()
        except (ValueError,TypeError,KeyError,AttributeError):
            return {'code':'INCOMPLETE_RESPONSE','stage':stage,'event':evidence}
        return {'code':'ACTIVE','stage':stage,'event':evidence}
    except AuthenticationRefused:
        return {'code':'AUTHENTICATION_UNAVAILABLE','stage':stage}
    except Exception as error:
        from googleapiclient.errors import HttpError
        import requests
        if isinstance(error,HttpError):
            return {'code':{403:'FORBIDDEN',404:'NOT_FOUND_404',410:'GONE_410'}.get(error.resp.status,'GOOGLE_ERROR'),'stage':stage}
        if isinstance(error,(TimeoutError,requests.exceptions.Timeout)):
            return {'code':'TIMEOUT','stage':stage}
        return {'code':'GOOGLE_UNAVAILABLE','stage':stage}


def conclusion(snapshot, observation=None, changed=False):
    if changed:
        return 'Record cambiato o rilettura non disponibile. '+INSUFFICIENT
    origin = snapshot.data if snapshot.kind == 'creation' else snapshot.creation
    if not origin or origin.get('execution_version') != 2:
        return 'Dati legacy o provenienza insufficiente. '+INSUFFICIENT
    if observation and observation['code'] in ('CONTEXT_MISMATCH','ORIGIN_MISMATCH','INVALID_RECORD','INVALID_EVENT_ID','LEGACY_OR_INSUFFICIENT_ORIGIN','CALENDAR_MISMATCH'):
        return INSUFFICIENT
    data = snapshot.data
    if data.get('google_write_started_at') is None:
        return ('Marcatore assente: nel protocollo corrente questo tentativo non ha avviato la scrittura. '
                'Non prova che l’evento non esista per altre operazioni; richiede esecutore fermo e provenienza integra.')
    positive = {'creation':'CREATED','reschedule':'UPDATED','deletion':'DELETED'}[snapshot.kind]
    if data['status'] == positive:
        return 'Il registro attesta un completamento positivo del flusso. Lo stato Google attuale può essere diverso.'
    if observation and observation['code'] == 'ACTIVE':
        if snapshot.kind == 'creation':
            return 'Creazione riscontrata: evento attivo con ID preassegnato nel contesto verificato. Nessuno stato PostgreSQL aggiornato.'
        if snapshot.kind == 'reschedule':
            event = observation['event']
            start,end = (_instant(datetime.fromisoformat(event[key]['dateTime'])) for key in ('start','end'))
            if (start,end) == (_instant(data['requested_start']),_instant(data['requested_end'])):
                return 'Intervallo richiesto osservato; non prova che questo tentativo abbia prodotto la modifica. '+INSUFFICIENT
            return 'Intervallo attuale diverso da quello richiesto. '+INSUFFICIENT
    if snapshot.kind != 'creation' and data['status'] == 'FAILED' and data.get('outcome') == 'STALE':
        return 'Il registro attesta un rifiuto per versione cambiata; non equivale a nessuna chiamata inviata.'
    return INSUFFICIENT


def diagnose(password, kind, operation_id, calendar_id=None, owner_ref=OWNER_REF,
             check_google=False, service_factory=local_google_service):
    snapshot = read_snapshot(password,kind,operation_id)
    if snapshot is None:
        return {'error':'OPERATION_NOT_FOUND'}
    result = {'postgresql':{'type':kind,'record':snapshot.data,'creation':snapshot.creation},
              'google':{'code':'NOT_REQUESTED'},'record_changed':False}
    if check_google:
        result['google'] = probe_google(snapshot,calendar_id,owner_ref,service_factory)
        try:
            after = read_snapshot(password,kind,operation_id)
            result['record_changed'] = after != snapshot
        except Exception:
            result['record_changed'] = True
            result['reread_error'] = 'POSTGRESQL_UNAVAILABLE'
    # Validate local provenance even without --check-google, without any network.
    problem = context_problem(snapshot,calendar_id,owner_ref)
    result['local_validation'] = problem or 'VALID'
    result['conclusion'] = conclusion(snapshot, result['google'] if not problem else {'code':problem},result['record_changed'])
    return result
