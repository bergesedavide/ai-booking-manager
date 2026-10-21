import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(
    0,
    str(PROJECT_ROOT / "google-calendar-prototype"),
)

from google_calendar import delete_booking


def test_primary_calendar_blocked():
    """La cancellazione sul calendario primary deve essere impedita."""

    service = MagicMock()

    with pytest.raises(ValueError, match="Calendar ID di test non valido"):
        delete_booking(
            service=service,
            calendar_id="primary",
            event_id="test-event-123",
            expected_etag='"etag-originale"',
        )

    # Nessuna richiesta deve essere inviata a Google Calendar.
    service.events.assert_not_called()

def test_delete_blocked_when_etag_changes():
    """Un evento modificato dopo la richiesta non deve essere cancellato."""

    service = MagicMock()

    # Simuliamo un evento Google Calendar modificato nel frattempo.
    service.events.return_value.get.return_value.execute.return_value = {
        "id": "test-event-123",
        "summary": "Allenamento Mario",
        "status": "confirmed",
        "start": {
            "dateTime": "2026-10-28T15:00:00+01:00"
        },
        "end": {
            "dateTime": "2026-10-28T16:00:00+01:00"
        },
        "etag": '"versione-2"',
    }

    # Il professionista aveva visualizzato la versione precedente.
    with pytest.raises(ValueError, match="L'appuntamento è cambiato"):
        delete_booking(
            service=service,
            calendar_id="test-calendar-id",
            event_id="test-event-123",
            expected_etag='"versione-1"',
        )

    # La richiesta DELETE non deve essere neppure preparata.
    service.events.return_value.delete.assert_not_called()

def test_delete_uses_if_match_header():
    """La cancellazione deve utilizzare l'ETag come precondizione HTTP."""

    service = MagicMock()

    event_id = "test-event-123"
    calendar_id = "test-calendar-id"
    etag = '"versione-1"'

    # Simuliamo un appuntamento esistente.
    service.events.return_value.get.return_value.execute.return_value = {
        "id": event_id,
        "summary": "Allenamento Mario",
        "status": "confirmed",
        "start": {
            "dateTime": "2026-10-28T15:00:00+01:00"
        },
        "end": {
            "dateTime": "2026-10-28T16:00:00+01:00"
        },
        "etag": etag,
    }

    # Simuliamo la richiesta HTTP di cancellazione.
    delete_request = MagicMock()
    delete_request.headers = {}

    service.events.return_value.delete.return_value = delete_request

    delete_booking(
        service=service,
        calendar_id=calendar_id,
        event_id=event_id,
        expected_etag=etag,
    )

    # Verifica che venga utilizzato il calendario previsto.
    service.events.return_value.delete.assert_called_once_with(
        calendarId=calendar_id,
        eventId=event_id,
    )

    # Verifica la presenza della protezione ETag.
    assert delete_request.headers["If-Match"] == etag

    # Verifica che la richiesta venga eseguita una sola volta.
    delete_request.execute.assert_called_once()

from googleapiclient.errors import HttpError
from httplib2 import Response


def test_delete_propagates_http_412():
    """Un errore HTTP 412 deve impedire una falsa conferma."""

    service = MagicMock()

    event_id = "test-event-123"
    calendar_id = "test-calendar-id"
    etag = '"versione-1"'

    service.events.return_value.get.return_value.execute.return_value = {
        "id": event_id,
        "summary": "Allenamento Mario",
        "status": "confirmed",
        "start": {
            "dateTime": "2026-10-28T15:00:00+01:00"
        },
        "end": {
            "dateTime": "2026-10-28T16:00:00+01:00"
        },
        "etag": etag,
    }

    delete_request = MagicMock()
    delete_request.headers = {}

    # Simula il rifiuto della cancellazione da parte di Google.
    delete_request.execute.side_effect = HttpError(
        Response({"status": 412}),
        b'{"error": {"message": "Precondition Failed"}}',
    )

    service.events.return_value.delete.return_value = delete_request

    with pytest.raises(HttpError) as error:
        delete_booking(
            service=service,
            calendar_id=calendar_id,
            event_id=event_id,
            expected_etag=etag,
        )

    assert error.value.resp.status == 412
    assert delete_request.headers["If-Match"] == etag
    delete_request.execute.assert_called_once()