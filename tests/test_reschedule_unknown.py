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


def test_reschedule_unknown():
    bot = load_bot()

    update = MagicMock()
    update.effective_user.id = bot.ALLOWED_USER_ID
    update.effective_message.reply_text = AsyncMock()

    context = MagicMock()
    context.args = ["test-event-123", "2026-10-28", "15:00"]
    context.user_data = {}

    original_event = {
        "id": "test-event-123",
        "summary": "Test UNKNOWN M04",
        "etag": '"test-etag"',
        "start": {
            "dateTime": "2026-10-28T10:00:00+01:00",
        },
        "end": {
            "dateTime": "2026-10-28T11:00:00+01:00",
        },
    }

    with (
        patch.object(bot, "authenticate"),
        patch.object(bot, "build"),
        patch.object(bot, "get_booking", return_value=original_event),
        patch.object(
            bot,
            "check_reschedule_availability_safe",
            return_value=bot.AvailabilityStatus.UNKNOWN,
        ),
        patch.object(bot, "update_booking") as mock_update,
    ):
        asyncio.run(bot.modifica(update, context))

        mock_update.assert_not_called()

    assert "pending_reschedules" not in context.user_data

    update.effective_message.reply_text.assert_awaited_once()

    response = (
        update.effective_message.reply_text.await_args.args[0]
    )

    assert "Disponibilità non verificabile" in response
    assert "La modifica non può essere autorizzata" in response

    print("TEST SUPERATO: UNKNOWN blocca la modifica.")


if __name__ == "__main__":
    test_reschedule_unknown()