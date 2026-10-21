import asyncio
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BOT_FILE = PROJECT_ROOT / "telegram-bot" / "bot.py"


def load_bot():
    spec = importlib.util.spec_from_file_location(
        "telegram_bot", BOT_FILE
    )

    if spec is None or spec.loader is None:
        raise RuntimeError("Impossibile caricare bot.py")

    bot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bot)

    return bot


def test_google_availability_error():
    bot = load_bot()

    update = MagicMock()
    update.effective_user.id = bot.ALLOWED_USER_ID

    update.effective_message = MagicMock()
    update.effective_message.reply_text = AsyncMock()

    context = MagicMock()
    context.args = [
        "2026-10-22",
        "18:00",
        "60",
        "Test",
        "Errore",
        "Google",
    ]

    with patch.object(bot, "authenticate"), \
         patch.object(bot, "build"), \
         patch.object(bot, "check_availability") as mock_check, \
         patch.object(bot, "create_booking") as mock_booking:

        mock_check.side_effect = RuntimeError(
            "Google Calendar non disponibile"
        )

        asyncio.run(bot.prenota(update, context))

        mock_booking.assert_not_called()

    update.effective_message.reply_text.assert_awaited()

    print(
        "TEST SUPERATO: errore Google bloccato, "
        "nessuna prenotazione creata."
    )


def test_google_creation_error():
    bot = load_bot()

    update = MagicMock()
    update.effective_user.id = bot.ALLOWED_USER_ID
    update.effective_message = MagicMock()
    update.effective_message.reply_text = AsyncMock()

    context = MagicMock()
    context.args = [
        "2026-10-22",
        "18:00",
        "60",
        "Test",
        "Creazione",
        "Fallita",
    ]

    with patch.object(bot, "authenticate"), \
         patch.object(bot, "build"), \
         patch.object(bot, "check_availability") as mock_check, \
         patch.object(bot, "create_booking") as mock_booking:

        # Google conferma che l'orario è libero.
        mock_check.return_value = True

        # Google restituisce un errore durante la creazione.
        mock_booking.side_effect = RuntimeError(
            "Errore durante la creazione su Google Calendar"
        )

        asyncio.run(bot.prenota(update, context))

        mock_check.assert_called_once()
        mock_booking.assert_called_once()

    update.effective_message.reply_text.assert_awaited()

    response = (
        update.effective_message.reply_text.await_args.args[0]
    )

    assert "Non sono riuscito a creare" in response

    print(
        "TEST SUPERATO: errore creazione Google gestito correttamente."
    )


if __name__ == "__main__":
    test_google_availability_error()
    test_google_creation_error()