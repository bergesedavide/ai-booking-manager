"""AI Calendar transport: separate reads and at most one write dispatch per attempt."""
from pathlib import Path
from threading import Lock
from urllib.parse import unquote, urlsplit
import re

import httplib2
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build as api_build
from googleapiclient.http import build_http

CONNECT_TIMEOUT = 5
READ_TIMEOUT = 15
TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)
HOSTS = frozenset(('www.googleapis.com', 'calendar.googleapis.com'))
EVENT_PATH = re.compile(r'^/calendar/v3/calendars/([^/]+)/events(?:/([^/]+))?$')


class RejectedCalendarRequest(RuntimeError):
    pass


class WriteAttemptConsumed(RuntimeError):
    pass


class WriteTransportError(RuntimeError):
    pass


class BoundedAuthRequest(Request):
    def __call__(self, *args, **kwargs):
        kwargs['timeout'] = TIMEOUT
        return super().__call__(*args, **kwargs)


def ensure_credentials(credentials):
    if not credentials.valid:
        if not credentials.refresh_token:
            raise WriteTransportError('Credenziali OAuth esistenti non utilizzabili')
        credentials.refresh(BoundedAuthRequest())
    if not credentials.valid:
        raise WriteTransportError('Credenziali OAuth esistenti non utilizzabili')


def load_existing_credentials():
    """No interactive OAuth, no new scopes and no token-file writes."""
    try:
        credentials = Credentials.from_authorized_user_file(str(Path(__file__).resolve().parent/'token.json'))
        ensure_credentials(credentials)
        return credentials
    except Exception:
        raise WriteTransportError('Autenticazione locale non disponibile') from None


def classify_request(uri, method, headers=None):
    parsed = urlsplit(uri)
    if (parsed.scheme != 'https' or parsed.hostname not in HOSTS or parsed.port not in (None,443)
            or parsed.username or parsed.password or parsed.fragment):
        raise RejectedCalendarRequest('Endpoint Calendar non consentito')
    if any(key.lower() == 'x-http-method-override' for key in (headers or {})):
        raise RejectedCalendarRequest('Override del metodo non consentito')
    method = method.upper()
    event = EVENT_PATH.fullmatch(parsed.path)
    if event:
        calendar_id, event_id = event.groups()
        if unquote(calendar_id).strip().lower() == 'primary':
            raise RejectedCalendarRequest('Calendario primary non consentito')
        if method == 'GET':
            return 'read'
        if (method == 'POST' and event_id is None) or (method in ('PATCH','DELETE') and event_id is not None):
            return 'write'
    if method == 'POST' and parsed.path == '/calendar/v3/freeBusy':
        return 'read'
    if method == 'GET' and (parsed.path == '/calendar/v3/users/me/calendarList'
                           or parsed.path.startswith('/calendar/v3/users/me/calendarList/')):
        return 'read'
    raise RejectedCalendarRequest('Combinazione metodo/endpoint non consentita')


class SingleWriteHttp:
    """One-shot httplib2-compatible interface backed by plain requests/urllib3."""
    def __init__(self, credentials):
        self.credentials = credentials
        self._used = False
        self._lock = Lock()

    def request(self, uri, method='GET', body=None, headers=None, **kwargs):
        with self._lock:
            if self._used:
                raise WriteAttemptConsumed('Tentativo HTTP già consumato')
            # Consume even on local/authentication failure. No retry of this attempt.
            self._used = True
        if classify_request(uri, method, headers) != 'write':
            raise RejectedCalendarRequest('Il trasporto monouso accetta solo scritture')
        if body is not None and not isinstance(body,(str,bytes)):
            raise RejectedCalendarRequest('Body non supportato')
        request_headers = dict(headers or {})
        if method.upper() in ('PATCH','DELETE') and not any(
                key.lower() == 'if-match' and isinstance(value,str) and value.strip()
                for key,value in request_headers.items()):
            raise RejectedCalendarRequest('If-Match obbligatorio')
        try:
            ensure_credentials(self.credentials)  # Only before the first Calendar send.
            self.credentials.apply(request_headers)  # No reactive OAuth wrapper.
            with requests.Session() as session:
                session.trust_env = False  # No implicit proxy/.netrc authentication.
                retry = Retry(total=0,connect=0,read=0,redirect=0,status=0,other=0,
                              status_forcelist=(),respect_retry_after_header=False)
                session.mount('https://',HTTPAdapter(max_retries=retry))
                response = session.request(method.upper(),uri,data=body,headers=request_headers,
                    timeout=TIMEOUT,allow_redirects=False,verify=True)
                try:
                    content = response.content
                    result = httplib2.Response({**dict(response.headers),'status':str(response.status_code)})
                    result.reason = response.reason or ''
                    return result,content
                finally:
                    response.close()
        except Exception:
            # Do not expose bearer tokens, bodies or URL parameters in exceptions.
            raise WriteTransportError('Esito HTTP Calendar non confermato') from None

    def close(self):
        pass  # The per-dispatch session is always closed inside request().


class CalendarHttp:
    """One AI service instance = one possible write attempt, plus separate reads."""
    def __init__(self, read_http, write_http):
        self.read_http, self.write_http = read_http,write_http

    def request(self, uri, method='GET', body=None, headers=None, **kwargs):
        route = classify_request(uri,method,headers)
        transport = self.write_http if route == 'write' else self.read_http
        return transport.request(uri,method=method,body=body,headers=headers,**kwargs)

    def close(self):
        try:
            self.read_http.close()
        finally:
            self.write_http.close()


def build_calendar_service(service_name, version, *, credentials):
    if (service_name,version) != ('calendar','v3'):
        raise RejectedCalendarRequest('Servizio non consentito')
    # AuthorizedHttp is confined to reads, including the FreeBusy POST.
    read_http = AuthorizedHttp(credentials,http=build_http())
    http = CalendarHttp(read_http,SingleWriteHttp(credentials))
    try:
        return api_build('calendar','v3',http=http,cache_discovery=False,num_retries=0)
    except Exception:
        http.close()
        raise
