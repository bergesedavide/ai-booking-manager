import asyncio
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BOT_FILE = PROJECT_ROOT / "telegram-bot" / "bot.py"


def load_bot():
    spec = importlib.util.spec_from_file_location(
        "telegram_bot",
        BOT_FILE,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError("Impossibile caricare bot.py")

    bot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bot)

    return bot


def test_booking_cancel() -> None:
    bot = load_bot()

    request_id = "1234567890abcdef"

    update = MagicMock()
    update.effective_user.id = bot.ALLOWED_USER_ID
    update.callback_query.data = f"booking_cancel:{request_id}"
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()

    context = MagicMock()
    context.user_data = {
        "pending_bookings": {
            request_id: {
                "title": "Test M03",
                "start": "2026-10-22T15:00:00+02:00",
                "duration": 60,
                "calendar_id": bot.CALENDAR_ID,
            }
        }
    }

    with patch.object(bot, "authenticate") as mock_auth:
        with patch.object(bot, "create_booking") as mock_booking:

            asyncio.run(
                bot.handle_booking_callback(update, context)
            )

            mock_auth.assert_not_called()
            mock_booking.assert_not_called()

    assert request_id not in context.user_data["pending_bookings"]

    update.callback_query.edit_message_text.assert_awaited_once()

    response = (
        update.callback_query.edit_message_text.await_args.args[0]
    )

    assert "Prenotazione annullata" in response

    print("TEST SUPERATO: prenotazione annullata correttamente.")


def test_booking_confirm() -> None:
    bot = load_bot()

    request_id = "abcdef1234567890"

    update = MagicMock()
    update.effective_user.id = bot.ALLOWED_USER_ID
    update.callback_query.data = f"booking_confirm:{request_id}"
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()

    context = MagicMock()
    context.user_data = {
        "pending_bookings": {
            request_id: {
                "title": "Test Conferma M03",
                "start": "2026-10-22T15:00:00+02:00",
                "duration": 60,
                "calendar_id": bot.CALENDAR_ID,
            }
        }
    }

    fake_event = {
        "id": "test-event-123",
        "status": "confirmed",
    }

    with patch.object(bot, "authenticate") as mock_auth, \
         patch.object(bot, "build") as mock_build, \
         patch.object(bot, "create_booking") as mock_booking:

        mock_booking.return_value = fake_event

        asyncio.run(
            bot.handle_booking_callback(update, context)
        )

        mock_auth.assert_called_once()
        mock_build.assert_called_once()
        mock_booking.assert_called_once()

        kwargs = mock_booking.call_args.kwargs

        assert kwargs["calendar_id"] == bot.CALENDAR_ID
        assert kwargs["title"] == "Test Conferma M03"
        assert kwargs["duration_minutes"] == 60

        assert kwargs["start"].isoformat() == (
            "2026-10-22T15:00:00+02:00"
        )

    assert request_id not in context.user_data["pending_bookings"]

    response = (
        update.callback_query.edit_message_text.await_args.args[0]
    )

    assert "Appuntamento creato" in response
    assert "test-event-123" in response

    print("TEST SUPERATO: conferma sovrapposizione corretta.")


def test_unauthorized_callback() -> None:
    bot = load_bot()

    request_id = "1234567890abcdef"

    update = MagicMock()
    update.effective_user.id = bot.ALLOWED_USER_ID + 1
    update.callback_query.data = f"booking_confirm:{request_id}"
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()

    context = MagicMock()
    context.user_data = {
        "pending_bookings": {
            request_id: {
                "title": "Test Sicurezza M03",
                "start": "2026-10-22T15:00:00+02:00",
                "duration": 60,
                "calendar_id": bot.CALENDAR_ID,
            }
        }
    }

    with patch.object(bot, "authenticate") as mock_auth, \
         patch.object(bot, "create_booking") as mock_booking:

        asyncio.run(
            bot.handle_booking_callback(update, context)
        )

        mock_auth.assert_not_called()
        mock_booking.assert_not_called()

    # La richiesta deve rimanere disponibile.
    assert request_id in context.user_data["pending_bookings"]

    # Nessun messaggio deve essere modificato.
    update.callback_query.edit_message_text.assert_not_awaited()

    # Telegram deve ricevere il rifiuto.
    update.callback_query.answer.assert_awaited_once_with(
        "Non autorizzato.",
        show_alert=True,
    )

    print("TEST SUPERATO: callback non autorizzato bloccato.")


def test_duplicate_confirmation() -> None:
    bot = load_bot()

    request_id = "abcdef1234567890"

    update = MagicMock()
    update.effective_user.id = bot.ALLOWED_USER_ID
    update.callback_query.data = f"booking_confirm:{request_id}"
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()

    context = MagicMock()
    context.user_data = {
        "pending_bookings": {
            request_id: {
                "title": "Test Duplicazione M03",
                "start": "2026-10-22T15:00:00+02:00",
                "duration": 60,
                "calendar_id": bot.CALENDAR_ID,
            }
        }
    }

    with patch.object(bot, "authenticate"), \
         patch.object(bot, "build"), \
         patch.object(bot, "create_booking") as mock_booking:

        mock_booking.return_value = {"id": "test-event-123"}

        # Prima conferma
        asyncio.run(
            bot.handle_booking_callback(update, context)
        )

        # Seconda conferma dello stesso pulsante
        asyncio.run(
            bot.handle_booking_callback(update, context)
        )

        # Google Calendar deve ricevere una sola richiesta.
        assert mock_booking.call_count == 1

    update.callback_query.answer.assert_any_await(
        "Richiesta già gestita o non più disponibile.",
        show_alert=True,
    )

    print("TEST SUPERATO: conferma duplicata bloccata.")


if __name__ == "__main__":
    test_booking_cancel()
    test_booking_confirm()
    test_unauthorized_callback()
    test_duplicate_confirmation()