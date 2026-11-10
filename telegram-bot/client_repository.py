"""Internal client persistence. Call through client_service after authorization.

No AI or Telegram I/O belongs inside these short transactions. Connections are
created, used and closed by the calling worker; no connection is shared.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Any

from database import get_connection


@dataclass(frozen=True)
class ClientData:
    first_name: str
    last_name: str
    phone: str | None
    email: str | None
    notes: str | None
    first_name_key: str
    last_name_key: str

    def values(self) -> tuple[str | None, ...]:
        return (self.first_name, self.last_name, self.phone, self.email, self.notes,
                self.first_name_key, self.last_name_key)


class ClientStatus(str, Enum):
    CREATED = 'CREATED'
    ALREADY_EXISTS = 'ALREADY_EXISTS'
    HOMONYM_REQUIRES_CONFIRMATION = 'HOMONYM_REQUIRES_CONFIRMATION'
    REQUEST_CONFLICT = 'REQUEST_CONFLICT'
    NOT_FOUND = 'NOT_FOUND'
    UNKNOWN = 'UNKNOWN'
    UNAVAILABLE = 'UNAVAILABLE'


@dataclass(frozen=True)
class ClientResult:
    status: ClientStatus
    client_id: int | None = None


class ClientStorageError(Exception):
    def __init__(self, commit_uncertain: bool = False):
        self.commit_uncertain = commit_uncertain
        super().__init__('Client persistence not confirmed')


class _ClientRepository:
    """Internal implementation, not an authorization boundary or Telegram API."""

    def __init__(self, password: str, connection_factory: Callable[[str], Any] = get_connection):
        self._password = password
        self._connect = connection_factory

    def insert(self, telegram_user_id: int, request_key: str, data: ClientData,
               allow_homonym: bool) -> ClientResult:
        committing = False
        try:
            with self._connect(self._password) as connection:
                with connection.cursor() as cursor:
                    cursor.execute('SHOW transaction_isolation')
                    if cursor.fetchone()[0] != 'read committed':
                        raise RuntimeError('READ COMMITTED required')
                    cursor.execute(
                        'INSERT INTO public.trainers (telegram_user_id) VALUES (%s) '
                        'ON CONFLICT (telegram_user_id) DO NOTHING', (telegram_user_id,))
                    # New snapshot after any concurrent creator commits. Lock also
                    # serializes distinct requests for the same trainer/name.
                    cursor.execute('SELECT id FROM public.trainers WHERE telegram_user_id=%s FOR UPDATE',
                                   (telegram_user_id,))
                    trainer_id = cursor.fetchone()[0]
                    cursor.execute(
                        'SELECT id,first_name,last_name,phone,email,notes,first_name_key,last_name_key '
                        'FROM public.clients WHERE trainer_id=%s AND request_key=%s',
                        (trainer_id, request_key))
                    existing = cursor.fetchone()
                    if existing:
                        status = (ClientStatus.ALREADY_EXISTS if tuple(existing[1:]) == data.values()
                                  else ClientStatus.REQUEST_CONFLICT)
                        result = ClientResult(status, existing[0])
                    else:
                        cursor.execute(
                            'SELECT EXISTS (SELECT 1 FROM public.clients WHERE trainer_id=%s '
                            'AND first_name_key=%s AND last_name_key=%s)',
                            (trainer_id, data.first_name_key, data.last_name_key))
                        homonym = cursor.fetchone()[0]
                        if homonym and not allow_homonym:
                            result = ClientResult(ClientStatus.HOMONYM_REQUIRES_CONFIRMATION)
                        else:
                            cursor.execute(
                                'INSERT INTO public.clients (trainer_id,request_key,first_name,last_name,'
                                'phone,email,notes,first_name_key,last_name_key) '
                                'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id',
                                (trainer_id, request_key, *data.values()))
                            result = ClientResult(ClientStatus.CREATED, cursor.fetchone()[0])
                committing = True
            return result  # Only report success after context-manager commit/close.
        except Exception:
            raise ClientStorageError(commit_uncertain=committing) from None

    def lookup(self, telegram_user_id: int, request_key: str) -> ClientResult:
        """Explicit read-only receipt lookup; never repeats a failed insertion."""
        try:
            with self._connect(self._password) as connection:
                with connection.cursor() as cursor:
                    cursor.execute('SET TRANSACTION READ ONLY')
                    cursor.execute(
                        'SELECT c.id FROM public.clients c JOIN public.trainers t ON t.id=c.trainer_id '
                        'WHERE t.telegram_user_id=%s AND c.request_key=%s',
                        (telegram_user_id, request_key))
                    row = cursor.fetchone()
                    result = (ClientResult(ClientStatus.ALREADY_EXISTS, row[0]) if row
                              else ClientResult(ClientStatus.NOT_FOUND))
            return result
        except Exception:
            raise ClientStorageError() from None
