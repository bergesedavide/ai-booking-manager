import asyncio
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


BOT_FILE = (
    Path(__file__).resolve().parent.parent
    / "telegram-bot"
    / "bot.py"
)


def test_unauthorized_user() -> None:
    spec = importlib.util.spec_from_file_location(
        "telegram_bot",
        BOT_FILE,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError("Impossibile caricare bot.py")

    bot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bot)

    update = MagicMock()
    update.effective_user.id = bot.ALLOWED_USER_ID + 1
    update.effective_message.reply_text = AsyncMock()

    context = MagicMock()

    asyncio.run(bot.start(update, context))

    update.effective_message.reply_text.assert_not_awaited()

    print("TEST SUPERATO: utente non autorizzato bloccato.")


def test_unauthorized_booking() -> None:
    spec = importlib.util.spec_from_file_location(
        "telegram_bot",
        BOT_FILE,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError("Impossibile caricare bot.py")

    bot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bot)

    update = MagicMock()
    update.effective_user.id = bot.ALLOWED_USER_ID + 1
    update.effective_message.reply_text = AsyncMock()

    context = MagicMock()
    context.args = [
        "2026-10-23",
        "15:00",
        "60",
        "Allenamento",
        "Test",
    ]

    with patch.object(bot, "authenticate") as mock_auth:
        with patch.object(bot, "create_booking") as mock_booking:
            asyncio.run(bot.prenota(update, context))

            mock_auth.assert_not_called()
            mock_booking.assert_not_called()

    update.effective_message.reply_text.assert_not_awaited()

    print("TEST SUPERATO: prenotazione non autorizzata bloccata.")


if __name__ == "__main__":
    test_unauthorized_user()
    test_unauthorized_booking()