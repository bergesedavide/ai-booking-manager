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


def test_callback_unknown():
    bot = load_bot()

    request_id = "1234567890abcdef"
    event_id = "test-event-123"

    update = MagicMock()
    update.effective_user.id = bot.ALLOWED_USER_ID
    update.callback_query.data = f"reschedule_confirm:{request_id}"
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()

    context = MagicMock()
    context.user_data = {
        "pending_reschedules": {
            request_id: {
                "event_id": event_id,
                "new_start": "2026-10-28T15:00:00+01:00",
                "expected_etag": '"test-etag"',
                "calendar_id": bot.CALENDAR_ID,
            }
        }
    }

    original_event = {
        "id": event_id,
        "summary": "Test callback UNKNOWN",
        "etag": '"test-etag"',
        "start": {
            "dateTime": "2026-10-28T10:00:00+01:00",
        },
        "end": {
            "dateTime": "2026-10-28T11:00:00+01:00",
        },
    }

    with (
        patch.object(bot, "authenticate") as mock_auth,
        patch.object(bot, "build") as mock_build,
        patch.object(bot, "get_booking", return_value=original_event),
        patch.object(
            bot,
            "check_reschedule_availability_safe",
            return_value=bot.AvailabilityStatus.UNKNOWN,
        ) as mock_availability,
        patch.object(bot, "update_booking") as mock_update,
    ):
        asyncio.run(
            bot.handle_reschedule_callback(update, context)
        )

        mock_auth.assert_called_once()
        mock_build.assert_called_once()
        mock_availability.assert_called_once()
        mock_update.assert_not_called()

    assert request_id not in context.user_data["pending_reschedules"]

    update.callback_query.edit_message_text.assert_awaited_once()

    response = (
        update.callback_query.edit_message_text.await_args.args[0]
    )

    assert "Disponibilità non verificabile" in response
    assert "La modifica non è stata applicata" in response

    print("TEST SUPERATO: UNKNOWN blocca la conferma della modifica.")


if __name__ == "__main__":
    test_callback_unknown()