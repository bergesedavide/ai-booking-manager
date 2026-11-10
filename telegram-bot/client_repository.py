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
    UNRESOLVED = 'UNRESOLVED'
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


@dataclass(frozen=True)
class _Checkpoint:
    trainer_id: int
    operation_id: int


class _ClientRepository:
    """Internal implementation, not an authorization boundary or Telegram API."""

    def __init__(self, password: str, connection_factory: Callable[[str], Any] = get_connection):
        self._password = password
        self._connect = connection_factory

    def schema_available(self) -> bool:
        """Short metadata-only check; never creates tables or trainer records."""
        try:
            with self._connect(self._password) as connection:
                with connection.cursor() as cursor:
                    cursor.execute('SET TRANSACTION READ ONLY')
                    cursor.execute("SELECT to_regclass('public.trainers'), to_regclass('public.clients'), to_regclass('public.client_operations')")
                    row = cursor.fetchone()
                    available = bool(row and row[0] and row[1] and row[2])
            return available
        except Exception:
            raise ClientStorageError() from None

    @staticmethod
    def _read_committed(cursor: Any) -> None:
        cursor.execute('SHOW transaction_isolation')
        if cursor.fetchone()[0] != 'read committed':
            raise RuntimeError('READ COMMITTED required')

    @staticmethod
    def _existing_client(cursor: Any, trainer_id: int, request_key: str,
                         data: ClientData, expected_id: int | None = None) -> ClientResult | None:
        cursor.execute(
            'SELECT id,first_name,last_name,phone,email,notes,first_name_key,last_name_key '
            'FROM public.clients WHERE trainer_id=%s AND request_key=%s',
            (trainer_id, request_key))
        row = cursor.fetchone()
        if expected_id is not None and (row is None or row[0] != expected_id):
            raise RuntimeError('Inconsistent operation receipt')
        if row:
            status = (ClientStatus.ALREADY_EXISTS if tuple(row[1:]) == data.values()
                      else ClientStatus.REQUEST_CONFLICT)
            return ClientResult(status, row[0])
        return None

    def insert(self, telegram_user_id: int, request_key: str, data: ClientData,
               allow_homonym: bool) -> ClientResult:
        # Only a checkpoint newly created and acknowledged in this invocation
        # permits _complete. No lookup/resume path can execute a stored PENDING.
        prepared = self._prepare(telegram_user_id, request_key, data, allow_homonym)
        if isinstance(prepared, ClientResult):
            return prepared
        return self._complete(telegram_user_id, request_key, data, prepared)

    def _prepare(self, telegram_user_id: int, request_key: str, data: ClientData,
                 allow_homonym: bool) -> _Checkpoint | ClientResult:
        committing = False
        try:
            with self._connect(self._password) as connection:
                with connection.cursor() as cursor:
                    self._read_committed(cursor)
                    cursor.execute(
                        'INSERT INTO public.trainers (telegram_user_id) VALUES (%s) '
                        'ON CONFLICT (telegram_user_id) DO NOTHING', (telegram_user_id,))
                    cursor.execute('SELECT id FROM public.trainers WHERE telegram_user_id=%s FOR UPDATE',
                                   (telegram_user_id,))
                    trainer_id = cursor.fetchone()[0]
                    cursor.execute(
                        'SELECT id,status,client_id FROM public.client_operations '
                        'WHERE trainer_id=%s AND request_key=%s', (trainer_id, request_key))
                    operation = cursor.fetchone()
                    if operation:
                        if operation[1] == 'PENDING':
                            prepared = ClientResult(ClientStatus.UNRESOLVED)
                        elif operation[1] == 'CREATED':
                            prepared = self._existing_client(cursor, trainer_id, request_key, data, operation[2])
                        else:
                            raise RuntimeError('Invalid operation status')
                    else:
                        # Keep legacy client deduplication; do not fabricate receipts.
                        prepared = self._existing_client(cursor, trainer_id, request_key, data)
                        if prepared is None:
                            cursor.execute(
                                'SELECT EXISTS (SELECT 1 FROM public.client_operations '
                                "WHERE trainer_id=%s AND first_name_key=%s AND last_name_key=%s AND status='PENDING')",
                                (trainer_id, data.first_name_key, data.last_name_key))
                            if cursor.fetchone()[0]:
                                prepared = ClientResult(ClientStatus.UNRESOLVED)
                            else:
                                cursor.execute(
                                    'SELECT EXISTS (SELECT 1 FROM public.clients WHERE trainer_id=%s '
                                    'AND first_name_key=%s AND last_name_key=%s)',
                                    (trainer_id, data.first_name_key, data.last_name_key))
                                if cursor.fetchone()[0] and not allow_homonym:
                                    prepared = ClientResult(ClientStatus.HOMONYM_REQUIRES_CONFIRMATION)
                                else:
                                    cursor.execute(
                                        'INSERT INTO public.client_operations '
                                        '(trainer_id,request_key,first_name_key,last_name_key,status) '
                                        "VALUES (%s,%s,%s,%s,'PENDING') RETURNING id",
                                        (trainer_id, request_key, data.first_name_key, data.last_name_key))
                                    prepared = _Checkpoint(trainer_id, cursor.fetchone()[0])
                committing = True
            return prepared
        except Exception:
            raise ClientStorageError(commit_uncertain=committing) from None

    def _complete(self, telegram_user_id: int, request_key: str, data: ClientData,
                  checkpoint: _Checkpoint) -> ClientResult:
        try:
            with self._connect(self._password) as connection:
                with connection.cursor() as cursor:
                    self._read_committed(cursor)
                    cursor.execute('SELECT id FROM public.trainers WHERE telegram_user_id=%s FOR UPDATE',
                                   (telegram_user_id,))
                    row = cursor.fetchone()
                    if not row or row[0] != checkpoint.trainer_id:
                        raise RuntimeError('Trainer changed')
                    cursor.execute(
                        'SELECT status,client_id,first_name_key,last_name_key FROM public.client_operations '
                        'WHERE trainer_id=%s AND request_key=%s AND id=%s FOR UPDATE',
                        (checkpoint.trainer_id, request_key, checkpoint.operation_id))
                    operation = cursor.fetchone()
                    if operation != ('PENDING', None, data.first_name_key, data.last_name_key):
                        raise RuntimeError('Checkpoint changed')
                    cursor.execute(
                        'INSERT INTO public.clients (trainer_id,request_key,first_name,last_name,'
                        'phone,email,notes,first_name_key,last_name_key) '
                        'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id',
                        (checkpoint.trainer_id, request_key, *data.values()))
                    client_id = cursor.fetchone()[0]
                    cursor.execute(
                        "UPDATE public.client_operations SET status='CREATED',client_id=%s,updated_at=now() "
                        "WHERE trainer_id=%s AND request_key=%s AND id=%s AND status='PENDING' "
                        'AND client_id IS NULL RETURNING id',
                        (client_id, checkpoint.trainer_id, request_key, checkpoint.operation_id))
                    if cursor.fetchone() is None:
                        raise RuntimeError('Operation completion rejected')
            return ClientResult(ClientStatus.CREATED, client_id)
        except Exception:
            # The checkpoint is already durable: even a confirmed rollback leaves
            # a blocking PENDING. Do not present this attempt as safe to repeat.
            raise ClientStorageError(commit_uncertain=True) from None

    def lookup(self, telegram_user_id: int, request_key: str) -> ClientResult:
        """Read durable receipts before AI; no writes, locks or resume capability."""
        try:
            with self._connect(self._password) as connection:
                with connection.cursor() as cursor:
                    cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
                    cursor.execute(
                        'SELECT o.status,o.client_id FROM public.client_operations o '
                        'JOIN public.trainers t ON t.id=o.trainer_id '
                        'WHERE t.telegram_user_id=%s AND o.request_key=%s',
                        (telegram_user_id, request_key))
                    operation = cursor.fetchone()
                    if operation and operation[0] == 'PENDING':
                        result = ClientResult(ClientStatus.UNRESOLVED)
                    else:
                        cursor.execute(
                            'SELECT c.id FROM public.clients c JOIN public.trainers t ON t.id=c.trainer_id '
                            'WHERE t.telegram_user_id=%s AND c.request_key=%s',
                            (telegram_user_id, request_key))
                        row = cursor.fetchone()
                        if operation and (operation[0] != 'CREATED' or row is None or row[0] != operation[1]):
                            raise RuntimeError('Inconsistent operation receipt')
                        result = (ClientResult(ClientStatus.ALREADY_EXISTS, row[0]) if row
                                  else ClientResult(ClientStatus.NOT_FOUND))
            return result
        except Exception:
            raise ClientStorageError() from None
