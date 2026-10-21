import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


PROJECT_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(
    0,
    str(PROJECT_ROOT / "google-calendar-prototype"),
)

BOT_PATH = PROJECT_ROOT / "telegram-bot" / "bot.py"

spec = importlib.util.spec_from_file_location(
    "booking_telegram_bot",
    BOT_PATH,
)

bot = importlib.util.module_from_spec(spec)

with patch.dict(
    "os.environ",
    {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_ALLOWED_USER_ID": "123456",
        "GOOGLE_TEST_CALENDAR_ID": "test-calendar-id",
    },
):
    spec.loader.exec_module(bot)


def test_delete_cancel_does_not_call_google():
    """Annullare una richiesta non deve modificare Google Calendar."""

    request_id = "0123456789abcdef"

    query = SimpleNamespace(
        data=f"delete_cancel:{request_id}",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )

    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(
            id=bot.ALLOWED_USER_ID,
        ),
    )

    pending = {
        request_id: {
            "event_id": "test-event-123",
            "calendar_id": "test-calendar-id",
            "expected_etag": '"versione-1"',
        }
    }

    context = SimpleNamespace(
        user_data={
            "pending_deletions": pending,
        }
    )

    with (
        patch.object(bot, "authenticate") as authenticate,
        patch.object(bot, "build") as build,
        patch.object(bot, "delete_booking") as delete_booking,
    ):
        asyncio.run(
            bot.handle_delete_callback(
                update,
                context,
            )
        )

        authenticate.assert_not_called()
        build.assert_not_called()
        delete_booking.assert_not_called()

    assert request_id not in pending

    query.edit_message_text.assert_awaited_once()

    message = query.edit_message_text.await_args.args[0]

    assert "Cancellazione annullata" in message


def test_delete_duplicate_confirmation_is_blocked():
    """Una richiesta già consumata non deve eseguire cancellazioni."""

    request_id = "0123456789abcdef"

    query = SimpleNamespace(
        data=f"delete_confirm:{request_id}",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )

    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(
            id=bot.ALLOWED_USER_ID,
        ),
    )

    # La richiesta non è più presente: è già stata gestita.
    context = SimpleNamespace(
        user_data={
            "pending_deletions": {},
        }
    )

    with (
        patch.object(bot, "authenticate") as authenticate,
        patch.object(bot, "build") as build,
        patch.object(bot, "delete_booking") as delete_booking,
    ):
        asyncio.run(
            bot.handle_delete_callback(update, context)
        )

        authenticate.assert_not_called()
        build.assert_not_called()
        delete_booking.assert_not_called()

    query.edit_message_text.assert_awaited_once()

    message = query.edit_message_text.await_args.args[0]

    assert "già stata gestita" in message


def test_delete_unauthorized_user_is_blocked():
    """Un utente non autorizzato non può confermare cancellazioni."""

    request_id = "0123456789abcdef"

    query = SimpleNamespace(
        data=f"delete_confirm:{request_id}",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )

    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(
            id=bot.ALLOWED_USER_ID + 1,
        ),
    )

    pending = {
        request_id: {
            "event_id": "test-event-123",
            "calendar_id": "test-calendar-id",
            "expected_etag": '"versione-1"',
        }
    }

    context = SimpleNamespace(
        user_data={
            "pending_deletions": pending,
        }
    )

    with (
        patch.object(bot, "authenticate") as authenticate,
        patch.object(bot, "build") as build,
        patch.object(bot, "delete_booking") as delete_booking,
    ):
        asyncio.run(
            bot.handle_delete_callback(update, context)
        )

        authenticate.assert_not_called()
        build.assert_not_called()
        delete_booking.assert_not_called()

    # La richiesta originale deve rimanere disponibile.
    assert request_id in pending

    # Telegram deve ricevere un avviso di accesso negato.
    query.answer.assert_awaited_once_with(
        "Non autorizzato.",
        show_alert=True,
    )

    # L'utente non autorizzato non può modificare il messaggio.
    query.edit_message_text.assert_not_awaited()


def test_delete_confirmation_blocked_when_etag_changes():
    """La conferma deve essere bloccata se l'evento è cambiato."""

    request_id = "0123456789abcdef"
    event_id = "test-event-123"

    query = SimpleNamespace(
        data=f"delete_confirm:{request_id}",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )

    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(
            id=bot.ALLOWED_USER_ID,
        ),
    )

    pending = {
        request_id: {
            "event_id": event_id,
            "calendar_id": "test-calendar-id",
            "expected_etag": '"versione-1"',
        }
    }

    context = SimpleNamespace(
        user_data={
            "pending_deletions": pending,
        }
    )

    # Simuliamo un evento modificato dopo la richiesta iniziale.
    modified_event = {
        "id": event_id,
        "summary": "Allenamento Mario",
        "etag": '"versione-2"',
        "start": {
            "dateTime": "2026-10-28T15:00:00+01:00"
        },
        "end": {
            "dateTime": "2026-10-28T16:00:00+01:00"
        },
    }

    with (
        patch.object(bot, "authenticate"),
        patch.object(bot, "build"),
        patch.object(
            bot,
            "get_booking",
            return_value=modified_event,
        ),
        patch.object(bot, "delete_booking") as delete_booking,
    ):
        asyncio.run(
            bot.handle_delete_callback(update, context)
        )

        # Nessuna cancellazione deve essere tentata.
        delete_booking.assert_not_called()

    # La richiesta deve essere consumata.
    assert request_id not in pending

    query.edit_message_text.assert_awaited_once()

    message = query.edit_message_text.await_args.args[0]

    assert "modificato" in message
    assert "Cancellazione bloccata" in message


def test_delete_confirmation_success():
    """Il bot conferma il successo solo dopo la verifica su Google."""

    request_id = "0123456789abcdef"
    event_id = "test-event-123"
    etag = '"versione-1"'

    query = SimpleNamespace(
        data=f"delete_confirm:{request_id}",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )

    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(
            id=bot.ALLOWED_USER_ID,
        ),
    )

    pending = {
        request_id: {
            "event_id": event_id,
            "calendar_id": "test-calendar-id",
            "expected_etag": etag,
        }
    }

    context = SimpleNamespace(
        user_data={
            "pending_deletions": pending,
        }
    )

    original_event = {
        "id": event_id,
        "summary": "Allenamento Mario",
        "etag": etag,
        "status": "confirmed",
        "start": {
            "dateTime": "2026-10-28T15:00:00+01:00"
        },
        "end": {
            "dateTime": "2026-10-28T16:00:00+01:00"
        },
    }

    # Dopo la cancellazione Google restituisce
    # un evento con stato "cancelled".
    cancelled_event = {
        "id": event_id,
        "status": "cancelled",
    }

    service = MagicMock()

    service.events.return_value.get.return_value.execute.return_value = (
        cancelled_event
    )

    with (
        patch.object(bot, "authenticate"),
        patch.object(bot, "build", return_value=service),
        patch.object(
            bot,
            "get_booking",
            return_value=original_event,
        ),
        patch.object(bot, "delete_booking") as delete_booking,
    ):
        asyncio.run(
            bot.handle_delete_callback(update, context)
        )

        delete_booking.assert_called_once_with(
            service=service,
            calendar_id="test-calendar-id",
            event_id=event_id,
            expected_etag=etag,
        )

    # Deve essere stata effettuata la verifica finale.
    service.events.return_value.get.assert_called_once_with(
        calendarId="test-calendar-id",
        eventId=event_id,
    )

    # La richiesta non deve essere riutilizzabile.
    assert request_id not in pending

    query.edit_message_text.assert_awaited_once()

    message = query.edit_message_text.await_args.args[0]

    assert "Appuntamento cancellato correttamente" in message


from googleapiclient.errors import HttpError
from httplib2 import Response


def test_delete_callback_handles_http_412():
    """Il bot deve gestire una cancellazione rifiutata da Google."""

    request_id = "0123456789abcdef"
    event_id = "test-event-123"
    etag = '"versione-1"'

    query = SimpleNamespace(
        data=f"delete_confirm:{request_id}",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )

    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(
            id=bot.ALLOWED_USER_ID,
        ),
    )

    pending = {
        request_id: {
            "event_id": event_id,
            "calendar_id": "test-calendar-id",
            "expected_etag": etag,
        }
    }

    context = SimpleNamespace(
        user_data={
            "pending_deletions": pending,
        }
    )

    original_event = {
        "id": event_id,
        "summary": "Allenamento Mario",
        "etag": etag,
        "status": "confirmed",
        "start": {
            "dateTime": "2026-10-28T15:00:00+01:00"
        },
        "end": {
            "dateTime": "2026-10-28T16:00:00+01:00"
        },
    }

    service = MagicMock()

    google_error = HttpError(
        Response({"status": 412}),
        b'{"error": {"message": "Precondition Failed"}}',
    )

    with (
        patch.object(bot, "authenticate"),
        patch.object(bot, "build", return_value=service),
        patch.object(
            bot,
            "get_booking",
            return_value=original_event,
        ),
        patch.object(
            bot,
            "delete_booking",
            side_effect=google_error,
        ) as delete_booking,
    ):
        asyncio.run(
            bot.handle_delete_callback(update, context)
        )

        delete_booking.assert_called_once()

    # Nessuna verifica finale: Google ha rifiutato il DELETE.
    service.events.return_value.get.assert_not_called()

    # La richiesta deve essere consumata.
    assert request_id not in pending

    query.edit_message_text.assert_awaited_once()

    message = query.edit_message_text.await_args.args[0]

    assert "Operazione bloccata" in message
    assert "cancellato correttamente" not in message

def test_delete_verification_failure_does_not_report_success():
    """Il bot non deve confermare cancellazioni non verificate."""

    request_id = "0123456789abcdef"
    event_id = "test-event-123"
    etag = '"versione-1"'

    query = SimpleNamespace(
        data=f"delete_confirm:{request_id}",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )

    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(
            id=bot.ALLOWED_USER_ID,
        ),
    )

    pending = {
        request_id: {
            "event_id": event_id,
            "calendar_id": "test-calendar-id",
            "expected_etag": etag,
        }
    }

    context = SimpleNamespace(
        user_data={
            "pending_deletions": pending,
        }
    )

    original_event = {
        "id": event_id,
        "summary": "Allenamento Mario",
        "etag": etag,
        "status": "confirmed",
        "start": {
            "dateTime": "2026-10-28T15:00:00+01:00"
        },
        "end": {
            "dateTime": "2026-10-28T16:00:00+01:00"
        },
    }

    service = MagicMock()

    # La verifica finale fallisce per un problema di rete.
    service.events.return_value.get.return_value.execute.side_effect = (
        ConnectionError("Connessione interrotta")
    )

    with (
        patch.object(bot, "authenticate"),
        patch.object(bot, "build", return_value=service),
        patch.object(
            bot,
            "get_booking",
            return_value=original_event,
        ),
        patch.object(bot, "delete_booking") as delete_booking,
    ):
        asyncio.run(
            bot.handle_delete_callback(update, context)
        )

        # La cancellazione è stata richiesta.
        delete_booking.assert_called_once()

    # Il bot ha tentato la verifica finale.
    service.events.return_value.get.assert_called_once_with(
        calendarId="test-calendar-id",
        eventId=event_id,
    )

    # La richiesta non può essere confermata nuovamente.
    assert request_id not in pending

    query.edit_message_text.assert_awaited_once()

    message = query.edit_message_text.await_args.args[0]

    # Il bot deve comunicare l'incertezza, non il successo.
    assert "Impossibile verificare" in message
    assert "cancellato correttamente" not in message


def test_delete_connection_error_does_not_report_success():
    """Un errore di connessione durante DELETE non equivale a successo."""

    request_id = "0123456789abcdef"
    event_id = "test-event-123"
    etag = '"versione-1"'

    query = SimpleNamespace(
        data=f"delete_confirm:{request_id}",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )

    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(
            id=bot.ALLOWED_USER_ID,
        ),
    )

    pending = {
        request_id: {
            "event_id": event_id,
            "calendar_id": "test-calendar-id",
            "expected_etag": etag,
        }
    }

    context = SimpleNamespace(
        user_data={
            "pending_deletions": pending,
        }
    )

    original_event = {
        "id": event_id,
        "summary": "Allenamento Mario",
        "etag": etag,
        "status": "confirmed",
        "start": {
            "dateTime": "2026-10-28T15:00:00+01:00"
        },
        "end": {
            "dateTime": "2026-10-28T16:00:00+01:00"
        },
    }

    service = MagicMock()

    with (
        patch.object(bot, "authenticate"),
        patch.object(bot, "build", return_value=service),
        patch.object(
            bot,
            "get_booking",
            return_value=original_event,
        ),
        patch.object(
            bot,
            "delete_booking",
            side_effect=ConnectionError("Connessione interrotta"),
        ) as delete_booking,
    ):
        asyncio.run(
            bot.handle_delete_callback(update, context)
        )

        delete_booking.assert_called_once()

    # La richiesta è stata consumata.
    assert request_id not in pending

    # Non deve essere comunicato un successo.
    query.edit_message_text.assert_awaited_once()

    message = query.edit_message_text.await_args.args[0]

    assert "esito della cancellazione non è confermato" in message
    assert "cancellato correttamente" not in message

    # Il messaggio deve invitare a verificare Google Calendar.
    assert "Controlla Google Calendar" in message