import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
PROTOTYPE_DIR = PROJECT_ROOT / "google-calendar-prototype"
sys.path.insert(0, str(PROTOTYPE_DIR))

from google_auth import authenticate
from google_calendar import (
    AvailabilityStatus,
    check_availability,
    check_reschedule_availability_safe,
    create_booking,
    get_booking,
    update_booking,
)


def load_config() -> tuple[str, int, str]:
    """Carica la configurazione locale senza dipendenze aggiuntive."""
    config: dict[str, str] = {}

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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        [InlineKeyboardButton("✅ Conferma comunque", callback_data="booking_confirm")],
        [InlineKeyboardButton("❌ Annulla", callback_data="booking_cancel")],
    ])


async def prenota(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Crea un appuntamento sul calendario Google di test."""
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return

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
            f"{date_string} {time_string}", "%Y-%m-%d %H:%M"
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
        service = build("calendar", "v3", credentials=credentials)
        available = check_availability(
            service=service,
            calendar_id=CALENDAR_ID,
            start=start,
            duration_minutes=duration,
        )

        if not available:
            request_id = os.urandom(8).hex()
            pending = context.user_data.setdefault("pending_bookings", {})
            pending[request_id] = {
                "title": title,
                "start": start.isoformat(),
                "duration": duration,
                "calendar_id": CALENDAR_ID,
            }
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "✅ Conferma comunque",
                    callback_data=f"booking_confirm:{request_id}",
                )],
                [InlineKeyboardButton(
                    "❌ Annulla",
                    callback_data=f"booking_cancel:{request_id}",
                )],
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
        await message.reply_text("Non sono riuscito a creare l'appuntamento.")
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
    if user.id != ALLOWED_USER_ID:
        await query.answer("Non autorizzato.", show_alert=True)
        return

    data = query.data or ""
    action, separator, request_id = data.partition(":")
    if not separator or action not in ("booking_confirm", "booking_cancel"):
        await query.answer("Richiesta non valida.", show_alert=True)
        return

    pending = context.user_data.get("pending_bookings", {})
    booking = pending.pop(request_id, None)
    if booking is None:
        await query.answer(
            "Richiesta già gestita o non più disponibile.", show_alert=True
        )
        return

    await query.answer()
    if action == "booking_cancel":
        await query.edit_message_text(
            "❌ Prenotazione annullata.\n"
            "Nessun appuntamento è stato creato."
        )
        return

    try:
        start = datetime.fromisoformat(booking["start"])
        credentials = authenticate()
        service = build("calendar", "v3", credentials=credentials)
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


async def modifica(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verifica la disponibilità per una modifica, senza applicarla."""
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return
    if user.id != ALLOWED_USER_ID:
        print("Tentativo di modifica non autorizzato.")
        return

    if len(context.args) != 3:
        await message.reply_text(
            "Formato corretto:\n/modifica EVENT_ID AAAA-MM-GG HH:MM"
        )
        return

    event_id, date_string, time_string = context.args
    if not event_id.strip():
        await message.reply_text("Google Event ID non valido.")
        return

    try:
        new_start = datetime.strptime(
            f"{date_string} {time_string}", "%Y-%m-%d %H:%M"
        ).replace(tzinfo=ZoneInfo("Europe/Rome"))

        # Rifiuta gli orari locali inesistenti o ambigui al cambio dell'ora.
        utc = ZoneInfo("UTC")
        rome = ZoneInfo("Europe/Rome")
        candidates = [new_start.replace(fold=fold) for fold in (0, 1)]
        valid_candidates = [
            candidate
            for candidate in candidates
            if candidate.astimezone(utc).astimezone(rome).replace(tzinfo=None)
            == new_start.replace(tzinfo=None)
        ]
        if (
            not valid_candidates
            or valid_candidates[0].utcoffset()
            != valid_candidates[-1].utcoffset()
        ):
            raise ValueError("Orario inesistente o ambiguo")
    except ValueError:
        await message.reply_text(
            "Data o ora non valide.\n"
            "Gli orari ambigui durante il cambio dell'ora non sono supportati.\n\n"
            "Esempio:\n/modifica EVENT_ID 2026-10-26 15:30"
        )
        return

    try:
        credentials = authenticate()
        service = build("calendar", "v3", credentials=credentials)
        event = get_booking(
            service=service,
            calendar_id=CALENDAR_ID,
            event_id=event_id,
        )
        title = event.get("summary", "Senza titolo")
        original_start = datetime.fromisoformat(
            event["start"]["dateTime"]
        ).astimezone(rome)
        original_end = datetime.fromisoformat(
            event["end"]["dateTime"]
        ).astimezone(rome)

        # La durata effettiva va calcolata in UTC (anche durante il cambio ora).
        duration = original_end.astimezone(utc) - original_start.astimezone(utc)
        duration_minutes = duration.total_seconds() / 60
        if duration_minutes <= 0 or not duration_minutes.is_integer():
            raise ValueError("Durata appuntamento non supportata")

        availability = check_reschedule_availability_safe(
            service=service,
            calendar_id=CALENDAR_ID,
            event_id=event_id,
            new_start=new_start,
            duration_minutes=int(duration_minutes),
        )
    except Exception:
        print("Errore durante il recupero o la verifica dell'appuntamento.")
        await message.reply_text(
            "❌ Impossibile recuperare o verificare l'appuntamento.\n"
            "Controlla il Google Event ID e riprova.\n\n"
            "Nessuna modifica effettuata."
        )
        return

    if availability == AvailabilityStatus.AVAILABLE:
        try:
            updated_event = update_booking(
                service=service,
                calendar_id=CALENDAR_ID,
                event_id=event_id,
                new_start=new_start,
                expected_etag=event["etag"],
            )

        except Exception as error:
            from googleapiclient.errors import HttpError

            if isinstance(error, HttpError) and error.resp.status == 412:
                await message.reply_text(
                    "⚠️ L'appuntamento è stato modificato nel frattempo.\n"
                    "La modifica non è stata applicata.\n\n"
                    "Riprova il comando /modifica."
                )
            else:
                print(f"Errore durante la modifica: {type(error).__name__}")
                await message.reply_text(
                    "❌ Non è stato possibile completare la modifica.\n"
                    "Controlla Google Calendar prima di riprovare."
                )

            return

        updated_start = datetime.fromisoformat(
            updated_event["start"]["dateTime"]
        ).astimezone(rome)

        updated_end = datetime.fromisoformat(
            updated_event["end"]["dateTime"]
        ).astimezone(rome)

        await message.reply_text(
            "✅ Appuntamento modificato!\n\n"
            f"Titolo: {title}\n"
            f"Google Event ID: {event_id}\n\n"
            "Nuovo orario:\n"
            f"{updated_start.strftime('%d/%m/%Y %H:%M')}"
            " → "
            f"{updated_end.strftime('%H:%M')}"
        )
        return

    elif availability == AvailabilityStatus.CONFLICT:
        # Identificativo univoco della richiesta di modifica.
        request_id = os.urandom(8).hex()

        # Conserva la richiesta in attesa di conferma.
        pending = context.user_data.setdefault(
            "pending_reschedules",
            {},
        )

        pending[request_id] = {
            "event_id": event_id,
            "new_start": new_start.isoformat(),
            "expected_etag": event["etag"],
            "calendar_id": CALENDAR_ID,
        }

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Conferma comunque",
                    callback_data=f"reschedule_confirm:{request_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ Annulla",
                    callback_data=f"reschedule_cancel:{request_id}",
                )
            ],
        ])

        await message.reply_text(
            "⚠️ Sovrapposizione rilevata!\n\n"
            f"Titolo: {title}\n"
            f"Google Event ID: {event_id}\n\n"
            "Orario attuale:\n"
            f"{original_start.strftime('%d/%m/%Y %H:%M')}"
            " → "
            f"{original_end.strftime('%H:%M')}\n\n"
            "Nuovo orario richiesto:\n"
            f"{new_start.strftime('%d/%m/%Y %H:%M')}\n\n"
            "Vuoi modificare comunque l'appuntamento?",
            reply_markup=keyboard,
        )

        return

    else:
        availability_message = (
            "❌ Disponibilità non verificabile.\n"
            "La modifica non può essere autorizzata."
        )

    await message.reply_text(
        "📅 Richiesta di modifica\n\n"
        f"Titolo: {title}\n"
        f"Google Event ID: {event_id}\n\n"
        "Orario attuale:\n"
        f"{original_start.strftime('%d/%m/%Y %H:%M')}"
        " → "
        f"{original_end.strftime('%H:%M')}\n\n"
        "Nuovo orario richiesto:\n"
        f"{new_start.strftime('%d/%m/%Y %H:%M')}\n\n"
        f"{availability_message}\n\n"
        "🧪 Modalità di test.\n"
        "Nessun appuntamento è stato modificato."
    )


async def handle_reschedule_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Gestisce provvisoriamente i pulsanti delle modifiche in conflitto."""

    user = update.effective_user
    query = update.callback_query

    if user is None or query is None:
        return

    if user.id != ALLOWED_USER_ID:
        await query.answer("Non autorizzato.", show_alert=True)
        return

    data = query.data or ""
    action, separator, request_id = data.partition(":")

    if (
        not separator
        or action not in ("reschedule_confirm", "reschedule_cancel")
        or len(request_id) != 16
    ):
        await query.answer("Richiesta non valida.", show_alert=True)
        return

    pending = context.user_data.get("pending_reschedules", {})

    if request_id not in pending:
        await query.answer(
            "Richiesta già gestita o non più disponibile.",
            show_alert=True,
        )
        return

    await query.answer()

    if action == "reschedule_cancel":
        pending.pop(request_id, None)

        await query.edit_message_text(
            "❌ Modifica annullata.\n"
            "Nessun appuntamento è stato modificato."
        )
        return

    # Consuma la richiesta per impedire conferme ripetute.
    request = pending.pop(request_id, None)

    if request is None:
        await query.edit_message_text(
            "⚠️ Richiesta non più disponibile."
        )
        return

    try:
        event_id = request["event_id"]
        new_start = datetime.fromisoformat(request["new_start"])
        expected_etag = request["expected_etag"]

        # Non consentire l'utilizzo di calendari diversi da quello configurato.
        if request["calendar_id"] != CALENDAR_ID:
            raise ValueError("Calendario della richiesta non valido.")

        credentials = authenticate()
        service = build(
            "calendar",
            "v3",
            credentials=credentials,
        )

        # Recupera la versione corrente dell'appuntamento.
        event = get_booking(
            service=service,
            calendar_id=CALENDAR_ID,
            event_id=event_id,
        )

        # Blocca la modifica se l'appuntamento è cambiato.
        if event.get("etag") != expected_etag:
            await query.edit_message_text(
                "⚠️ L'appuntamento è cambiato nel frattempo.\n"
                "La modifica non è stata applicata.\n\n"
                "Riprova il comando /modifica."
            )
            return

        original_start = datetime.fromisoformat(
            event["start"]["dateTime"]
        )

        original_end = datetime.fromisoformat(
            event["end"]["dateTime"]
        )

        duration = (
            original_end.astimezone(timezone.utc)
            - original_start.astimezone(timezone.utc)
        )

        duration_minutes = int(duration.total_seconds() / 60)

        availability = check_reschedule_availability_safe(
            service=service,
            calendar_id=CALENDAR_ID,
            event_id=event_id,
            new_start=new_start,
            duration_minutes=duration_minutes,
        )

        if availability == AvailabilityStatus.UNKNOWN:
            await query.edit_message_text(
                "❌ Disponibilità non verificabile.\n"
                "La modifica non è stata applicata.\n\n"
                "Riprova il comando /modifica."
            )
            return

        # AVAILABLE: modifica consentita.
        # CONFLICT: modifica autorizzata esplicitamente dall'utente.
        updated_event = update_booking(
            service=service,
            calendar_id=CALENDAR_ID,
            event_id=event_id,
            new_start=new_start,
            expected_etag=expected_etag,
        )

        updated_start = datetime.fromisoformat(
            updated_event["start"]["dateTime"]
        ).astimezone(ZoneInfo("Europe/Rome"))

        updated_end = datetime.fromisoformat(
            updated_event["end"]["dateTime"]
        ).astimezone(ZoneInfo("Europe/Rome"))

        await query.edit_message_text(
            "✅ Appuntamento modificato!\n\n"
            f"Titolo: {updated_event.get('summary', 'Senza titolo')}\n"
            f"Google Event ID: {event_id}\n\n"
            "Nuovo orario:\n"
            f"{updated_start.strftime('%d/%m/%Y %H:%M')}"
            " → "
            f"{updated_end.strftime('%H:%M')}"
        )

    except Exception as error:
        from googleapiclient.errors import HttpError

        if isinstance(error, HttpError) and error.resp.status == 412:
            await query.edit_message_text(
                "⚠️ L'appuntamento è stato modificato nel frattempo.\n"
                "La modifica non è stata applicata.\n\n"
                "Riprova il comando /modifica."
            )
        else:
            print(
                "Errore durante la conferma della modifica:",
                type(error).__name__,
                str(error),
            )

            await query.edit_message_text(
                "❌ Non è stato possibile completare la modifica.\n"
                "Controlla Google Calendar prima di riprovare."
            )


def main() -> None:
    print("AI Booking Manager — Telegram Bot")
    print("Avvio polling Telegram...")
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("prenota", prenota))
    application.add_handler(CommandHandler("modifica", modifica))
    application.add_handler(
        CallbackQueryHandler(
            handle_booking_callback,
            pattern=r"^booking_(confirm|cancel):[0-9a-f]{16}$",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            handle_reschedule_callback,
            pattern=r"^reschedule_(confirm|cancel):[0-9a-f]{16}$",
        )
    )
    application.run_polling()


if __name__ == "__main__":
    main()
