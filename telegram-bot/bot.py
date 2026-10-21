import asyncio, os, sys
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from googleapiclient.discovery import build


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
PROTOTYPE_DIR = PROJECT_ROOT / "google-calendar-prototype"
sys.path.insert(0, str(PROTOTYPE_DIR))

from google_auth import authenticate
from google_calendar import create_booking, check_availability


def load_config() -> tuple[str, int]:
    """Carica la configurazione locale senza dipendenze aggiuntive."""
    config = {}

    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()

        if not line or line.startswith("#"):
            continue

        key, separator, value = line.partition("=")

        if separator:
            config[key.strip()] = value.strip()

    token = config.get("TELEGRAM_BOT_TOKEN", "")
    allowed_id = config.get("TELEGRAM_ALLOWED_USER_ID", "")

    if not token or not allowed_id:
        raise ValueError("Configurazione Telegram incompleta")

    calendar_id = config.get("GOOGLE_TEST_CALENDAR_ID", "")

    if not calendar_id or calendar_id == "primary":
        raise ValueError("Calendario Google di test non configurato")

    return token, int(allowed_id), calendar_id


BOT_TOKEN, ALLOWED_USER_ID, CALENDAR_ID = load_config()


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Risponde esclusivamente all'utente autorizzato."""

    user = update.effective_user
    message = update.effective_message

    if user is None or message is None:
        return

    if user.id != ALLOWED_USER_ID:
        print("Tentativo di accesso non autorizzato.")
        return

    await message.reply_text(
        "AI Booking Manager attivo!\n"
        "Autenticazione Telegram riuscita."
    )


def overlap_keyboard() -> InlineKeyboardMarkup:
    """Crea i pulsanti per confermare o annullare una sovrapposizione."""

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Conferma comunque",
                callback_data="booking_confirm",
            ),
        ],
        [
            InlineKeyboardButton(
                "❌ Annulla",
                callback_data="booking_cancel",
            ),
        ],
    ])


async def prenota(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Crea un appuntamento sul calendario Google di test."""

    user = update.effective_user
    message = update.effective_message

    if user is None or message is None:
        return

    # Controllo accessi prima di qualsiasi operazione Google.
    if user.id != ALLOWED_USER_ID:
        print("Tentativo di prenotazione non autorizzato.")
        return

    if len(context.args) < 4:
        await message.reply_text(
            "Formato corretto:\n"
            "/prenota AAAA-MM-GG HH:MM DURATA TITOLO"
        )
        return

    try:
        date_string = context.args[0]
        time_string = context.args[1]
        duration = int(context.args[2])
        title = " ".join(context.args[3:])

        start = datetime.strptime(
            f"{date_string} {time_string}",
            "%Y-%m-%d %H:%M",
        ).replace(tzinfo=ZoneInfo("Europe/Rome"))

        if duration <= 0:
            raise ValueError("Durata non valida")

    except ValueError:
        await message.reply_text(
            "Data, ora o durata non valide.\n"
            "Esempio: /prenota 2026-10-23 15:00 60 Allenamento Mario"
        )
        return

    try:
        credentials = authenticate()

        service = build(
            "calendar",
            "v3",
            credentials=credentials,
        )

        available = check_availability(
            service=service,
            calendar_id=CALENDAR_ID,
            start=start,
            duration_minutes=duration,
        )

        if not available:
            # Genera un identificativo univoco per questa richiesta.
            request_id = os.urandom(8).hex()

            # Conserva temporaneamente i dati della prenotazione.
            pending = context.user_data.setdefault(
                "pending_bookings", {}
            )

            pending[request_id] = {
                "title": title,
                "start": start.isoformat(),
                "duration": duration,
                "calendar_id": CALENDAR_ID,
            }

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "✅ Conferma comunque",
                        callback_data=f"booking_confirm:{request_id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "❌ Annulla",
                        callback_data=f"booking_cancel:{request_id}",
                    ),
                ],
            ])

            await message.reply_text(
                "⚠️ Sovrapposizione rilevata!\n\n"
                f"Titolo: {title}\n"
                f"Data: {date_string}\n"
                f"Ora: {time_string}\n"
                f"Durata: {duration} minuti\n\n"
                "Vuoi creare comunque l'appuntamento?",
                reply_markup=keyboard,
            )
            return

        event = create_booking(
            service=service,
            calendar_id=CALENDAR_ID,
            title=title,
            start=start,
            duration_minutes=duration,
        )

    except Exception:
        print("Errore durante la creazione dell'appuntamento.")
        await message.reply_text(
            "Non sono riuscito a creare l'appuntamento."
        )
        return

    await message.reply_text(
        "✅ Appuntamento creato!\n\n"
        f"Titolo: {title}\n"
        f"Data: {date_string}\n"
        f"Ora: {time_string}\n"
        f"Durata: {duration} minuti\n"
        f"Google Event ID: {event['id']}"
    )


async def handle_booking_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Gestisce la conferma o l'annullamento di una sovrapposizione."""

    user = update.effective_user
    query = update.callback_query

    if user is None or query is None:
        return

    # Verifica l'identità prima di qualsiasi operazione.
    if user.id != ALLOWED_USER_ID:
        await query.answer("Non autorizzato.", show_alert=True)
        return

    data = query.data or ""
    action, separator, request_id = data.partition(":")

    if not separator or action not in (
        "booking_confirm",
        "booking_cancel",
    ):
        await query.answer("Richiesta non valida.", show_alert=True)
        return

    pending = context.user_data.get("pending_bookings", {})
    booking = pending.pop(request_id, None)

    if booking is None:
        await query.answer(
            "Richiesta già gestita o non più disponibile.",
            show_alert=True,
        )
        return

    await query.answer()

    if action == "booking_cancel":
        await query.edit_message_text(
            "❌ Prenotazione annullata.\n"
            "Nessun appuntamento è stato creato."
        )
        return

    # La conferma autorizza esplicitamente la sovrapposizione.
    try:
        start = datetime.fromisoformat(booking["start"])

        credentials = authenticate()
        service = build(
            "calendar",
            "v3",
            credentials=credentials,
        )

        event = create_booking(
            service=service,
            calendar_id=CALENDAR_ID,
            title=booking["title"],
            start=start,
            duration_minutes=booking["duration"],
        )

    except Exception:
        print("Errore durante la conferma della prenotazione.")
        await query.edit_message_text(
            "❌ Errore durante la creazione dell'appuntamento.\n"
            "Controlla Google Calendar prima di riprovare."
        )
        return

    await query.edit_message_text(
        "✅ Appuntamento creato nonostante la sovrapposizione!\n\n"
        f"Titolo: {booking['title']}\n"
        f"Data e ora: {start.strftime('%d/%m/%Y %H:%M')}\n"
        f"Durata: {booking['duration']} minuti\n"
        f"Google Event ID: {event['id']}"
    )


def main() -> None:
    print("AI Booking Manager — Telegram Bot")
    print("Avvio polling Telegram...")

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("prenota", prenota))
    application.add_handler(
        CallbackQueryHandler(
            handle_booking_callback,
            pattern=r"^booking_(confirm|cancel):[0-9a-f]{16}$",
        )
    )

    application.run_polling()


if __name__ == "__main__":
    main()