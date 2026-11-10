"""Structured client extraction only: no identity, permissions or persistence."""
from dataclasses import dataclass
from typing import Literal
import unicodedata


class ClientResponseError(ValueError):
    """Invalid model output; messages never include personal data."""


class UnsupportedClientIntent(ClientResponseError):
    """The model did not identify a client registration request."""


@dataclass(frozen=True)
class ClientIntent:
    operation: Literal['add_client']
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    email: str | None = None
    notes: str | None = None

    @property
    def missing_fields(self) -> tuple[str, ...]:
        return tuple(field for field in ('first_name', 'last_name')
                     if getattr(self, field) is None)


LIMITS = {'first_name': 100, 'last_name': 100, 'phone': 30, 'email': 254, 'notes': 2000}


def validate_client_intent(data: object) -> ClientIntent:
    if not isinstance(data, dict) or set(data) - {'operation', *LIMITS}:
        raise ClientResponseError('Formato risposta clienti non valido')
    operation = data.get('operation')
    if not isinstance(operation, str):
        raise ClientResponseError('Intento clienti mancante o non valido')
    if operation != 'add_client':
        raise UnsupportedClientIntent('Richiesta non riconosciuta come inserimento cliente')
    values: dict[str, str | None] = {}
    for field, limit in LIMITS.items():
        value = data.get(field)
        if value is None:
            values[field] = None
            continue
        if not isinstance(value, str) or '\x00' in value:
            raise ClientResponseError(f'Campo {field} non valido')
        value = unicodedata.normalize('NFC', value.strip())
        if not value or len(value) > limit:
            raise ClientResponseError(f'Campo {field} vuoto o troppo lungo')
        values[field] = value
    return ClientIntent(operation='add_client', **values)
