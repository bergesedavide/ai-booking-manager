"""Client insertion API. Caller MUST authorize the Telegram identity first.

Identity and allow_homonym are trusted caller context, never model output.
This module does not authorize users or enable any Telegram entrypoint.
Call synchronous methods through await_worker when integrating async handlers.
"""
import unicodedata
from typing import Any, Callable

from database import get_connection
from client_repository import (
    _ClientRepository, ClientData, ClientResult, ClientStatus, ClientStorageError,
)


def _identity(value: int) -> int:
    if type(value) is not int or not 0 < value <= 9223372036854775807:
        raise ValueError('Invalid authorized Telegram identity')
    return value


def _text(value: str | None, field: str, limit: int | None = None,
          required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ValueError(f'{field} is required')
        return None
    if not isinstance(value, str) or '\x00' in value:
        raise ValueError(f'{field} is invalid')
    value = unicodedata.normalize('NFC', value.strip())
    if not value:
        if required:
            raise ValueError(f'{field} is required')
        return None
    if limit is not None and len(value) > limit:
        raise ValueError(f'{field} is too long')
    return value


def _request_key(value: str) -> str:
    # This opaque delivery identity must be preserved exactly, never rewritten.
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise ValueError('request_key is invalid')
    return value


def _name_key(value: str) -> str:
    # Preserve accents/punctuation; no fuzzy or transliterated matching.
    return unicodedata.normalize('NFC', ' '.join(value.casefold().split()))


class ClientService:
    def __init__(self, password: str, *, connection_factory: Callable[[str], Any] = get_connection):
        self._repository = _ClientRepository(password, connection_factory)

    def add_client(self, *, authorized_telegram_user_id: int, request_key: str,
                   first_name: str, last_name: str, phone: str | None = None,
                   email: str | None = None, notes: str | None = None,
                   allow_homonym: bool = False) -> ClientResult:
        identity = _identity(authorized_telegram_user_id)
        key = _request_key(request_key)
        if type(allow_homonym) is not bool:
            raise ValueError('allow_homonym must be explicit boolean caller context')
        first = _text(first_name, 'first_name', 100, required=True)
        last = _text(last_name, 'last_name', 100, required=True)
        assert first is not None and last is not None
        data = ClientData(first, last, _text(phone, 'phone', 30), _text(email, 'email', 254),
                          _text(notes, 'notes', 2000), _name_key(first), _name_key(last))
        try:
            return self._repository.insert(identity, key, data, allow_homonym)
        except ClientStorageError as error:
            return ClientResult(ClientStatus.UNKNOWN if error.commit_uncertain else ClientStatus.UNAVAILABLE)

    def lookup_request(self, *, authorized_telegram_user_id: int, request_key: str) -> ClientResult:
        """NOT_FOUND is a snapshot, not proof an uncertain commit can never arrive."""
        identity = _identity(authorized_telegram_user_id)
        key = _request_key(request_key)
        try:
            return self._repository.lookup(identity, key)
        except ClientStorageError:
            return ClientResult(ClientStatus.UNAVAILABLE)
