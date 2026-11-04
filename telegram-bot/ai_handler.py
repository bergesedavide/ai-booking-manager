"""Integrazione dell'agente AI con Telegram.

M06.3.1: interpretazione e riepilogo, senza operazioni su Google Calendar.
"""

import asyncio
from pathlib import Path
import sys
import secrets

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai.conversation import ConversationManager
from ai.interpreter import AppointmentInterpreter
from ai.ollama_client import OllamaClient, OllamaError
from ai.intent_checks import missing_creation_fields
from availability_service import check_ai_availability, AvailabilityStatus
from booking_service import BookingStatus, process_booking
from booking_repository import (
    RegistrationResult,
    register_booking,
    get_booking_operation,
    assign_google_event_id,
    mark_booking_failed,
    mark_booking_unknown
)
from booking_datetime import parse_ai_booking_start
from booking_repository import mark_booking_created
from database import get_connection


interpreter = AppointmentInterpreter(OllamaClient())

conversations: dict[int, ConversationManager] = {}
conversation_locks = asyncio.Lock()

async def handle_ai_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    allowed_user_id: int,
    calendar_id: str,
    postgres_password: str,
) -> None:
    """Gestisce il primo messaggio naturale dell'utente autorizzato."""

    user = update.effective_user
    message = update.effective_message

    if user is None or message is None or not message.text:
        return

    if user.id != allowed_user_id:
        print("Tentativo di accesso AI non autorizzato.")
        return

    user_id = user.id

    request_key = f"ai:{message.chat_id}:{message.message_id}"

    async with conversation_locks:
        try:
            manager = conversations.get(user_id)

            # Se esiste una conversazione, gestiamo la risposta.
            if manager is not None and manager.current_intent is not None:

                handled = await asyncio.to_thread(
                    manager.handle_reply,
                    message.text,
                    interpreter,
                    request_key,
                )

                # L'utente ha annullato la conversazione.
                if manager.current_intent is None:
                    conversations.pop(user_id, None)
                    await message.reply_text(
                        "❌ Conversazione annullata."
                    )
                    return

                if not handled:
                    await message.reply_text(
                        "⚠️ Non ho capito la risposta.\n"
                        f"{manager.next_question() or 'Riformula la richiesta.'}"
                    )
                    return

                question = manager.next_question()

                if question is not None:
                    await message.reply_text(question)
                    return

                intent = manager.current_intent

                await send_ai_result(
                    update,
                    context,
                    intent,
                    calendar_id,
                    manager.request_key,
                    postgres_password,
                )

                manager.reset()
                conversations.pop(user_id, None)
                return

            # Nessuna conversazione attiva: interpretiamo il messaggio.
            intent = await asyncio.to_thread(
                interpreter.interpret,
                message.text,
            )

            manager = ConversationManager()
            manager.start(intent, request_key)
            conversations[user_id] = manager

            question = manager.next_question()

            if question is not None:
                await message.reply_text(question)
                return

            await send_ai_result(
                update,
                context,
                intent,
                calendar_id,
                manager.request_key,
                postgres_password,
            )

            manager.reset()
            conversations.pop(user_id, None)

        except Exception as error:
            print(f"Errore agente AI: {type(error).__name__}: {error}")

            # Elimina eventuale stato conversazionale incompleto.
            conversations.pop(user_id, None)

            await message.reply_text(
                "⚠️ Si è verificato un errore durante l'elaborazione AI.\n"
                "La conversazione è stata interrotta.\n\n"
                "Puoi inviare una nuova richiesta per ricominciare."
            )


def format_intent_summary(intent) -> str:
    """Genera un riepilogo senza effettuare operazioni sul calendario."""

    duration = (
        f"{intent.durata_minuti} minuti"
        if intent.durata_minuti is not None
        else "Non specificata"
    )

    return (
        "📋 Riepilogo richiesta AI\n\n"
        f"Operazione: {intent.operazione}\n"
        f"Cliente: {intent.cliente or 'Non specificato'}\n"
        f"Data: {intent.data or 'Non specificata'}\n"
        f"Orario: {intent.ora or 'Non specificato'}\n"
        f"Durata: {duration}\n\n"
    )

async def format_ai_result(
    intent,
    calendar_id: str,
    request_key: str,
    postgres_password: str,
) -> str:
    """Verifica la disponibilità e gestisce la creazione AI."""

    if not request_key or not request_key.strip():
        raise ValueError("request_key AI mancante")

    summary = format_intent_summary(intent)

    # Le operazioni diverse dalla creazione rimangono in sola lettura.
    if intent.operazione != "crea":
        return summary

    # Sicurezza: mai scrivere sul calendario principale.
    if not calendar_id or calendar_id == "primary":
        return summary + "\n❌ Calendario di test non valido."

    # 1. Controllo preventivo dei duplicati.
    try:
        with get_connection(postgres_password) as connection:
            existing = get_booking_operation(
                connection,
                request_key,
            )

        if existing is not None:
            status, _ = existing

            if status == "CREATED":
                return (
                    summary
                    + "\n✅ Questo appuntamento risulta già registrato."
                )

            if status == "FAILED":
                return (
                    summary
                    + "\n❌ Questa richiesta è già stata elaborata "
                    "senza creare un appuntamento."
                )

            return (
                summary
                + "\n⚠️ Richiesta già registrata con stato "
                f"{status}. Nessuna nuova creazione eseguita."
            )

    except Exception as error:
        print(
            "Errore controllo duplicati: "
            f"{type(error).__name__}: {error}"
        )

        return (
            summary
            + "\n⚠️ Impossibile verificare le richieste precedenti. "
            "Nessun appuntamento creato."
        )

    # 2. Verifica disponibilità.
    availability = await asyncio.to_thread(
        check_ai_availability,
        intent,
        calendar_id,
    )

    if availability == AvailabilityStatus.CONFLICT:
        return (
            summary
            + "\n❌ L'orario richiesto è occupato.\n"
            "Nessun appuntamento creato."
        )

    if availability != AvailabilityStatus.AVAILABLE:
        return (
            summary
            + "\n⚠️ Impossibile verificare la disponibilità.\n"
            "Nessun appuntamento creato."
        )

    # 3. Validazione dei dati.
    try:
        start = parse_ai_booking_start(intent)

        if not intent.cliente or not intent.cliente.strip():
            raise ValueError("Cliente mancante")

        if (
            type(intent.durata_minuti) is not int
            or intent.durata_minuti <= 0
        ):
            raise ValueError("Durata non valida")

    except ValueError as error:
        return (
            summary
            + f"\n❌ Dati della prenotazione non validi: {error}"
        )

    # 4. Registrazione PostgreSQL prima della scrittura Google.
    try:
        registration = await asyncio.to_thread(
            register_ai_booking,
            request_key,
            postgres_password,
        )

        if registration == RegistrationResult.ALREADY_EXISTS:
            return (
                summary
                + "\n⚠️ Richiesta già registrata. "
                "Nessuna nuova prenotazione eseguita."
            )

        with get_connection(postgres_password) as connection:
            operation = get_booking_operation(
                connection,
                request_key,
            )

        if (
            operation is None
            or operation[0] != "PENDING"
            or not operation[1]
        ):
            raise RuntimeError(
                "Operazione PostgreSQL non valida"
            )

        google_event_id = operation[1]

    except Exception as error:
        print(
            "Errore registrazione PostgreSQL: "
            f"{type(error).__name__}: {error}"
        )

        return (
            summary
            + "\n⚠️ Impossibile preparare la prenotazione. "
            "Nessuna scrittura Google avviata."
        )

    # 5. Creazione Google Calendar.
    # Da questo punto l'esito potrebbe diventare incerto.
    try:
        result = await asyncio.to_thread(
            process_booking,
            calendar_id,
            f"Allenamento - {intent.cliente}",
            start,
            intent.durata_minuti,
            google_event_id,
        )

    except Exception as error:
        print(
            "Errore chiamata servizio prenotazione: "
            f"{type(error).__name__}: {error}"
        )

        result = None

    # 6. Aggiornamento dello stato PostgreSQL.
    try:
        if (
            result is not None
            and result.status == BookingStatus.CREATED
            and result.event_id == google_event_id
        ):
            with get_connection(postgres_password) as connection:
                saved = mark_booking_created(
                    connection,
                    request_key,
                    google_event_id,
                )

            if not saved:
                raise RuntimeError(
                    "Impossibile confermare lo stato CREATED"
                )

            return (
                summary
                + "\n✅ Appuntamento creato su Google Calendar!\n"
                f"Google Event ID: {google_event_id}"
            )

        if (
            result is not None
            and result.status == BookingStatus.CONFLICT
        ):
            with get_connection(postgres_password) as connection:
                saved = mark_booking_failed(
                    connection,
                    request_key,
                )

            if not saved:
                raise RuntimeError(
                    "Impossibile registrare il conflitto"
                )

            return (
                summary
                + "\n❌ L'orario è diventato occupato.\n"
                "Nessun appuntamento creato."
            )

        # Tutti gli altri risultati sono trattati come incerti.
        # Include UNKNOWN, eccezioni e ID Google inattesi.
        with get_connection(postgres_password) as connection:
            saved = mark_booking_unknown(
                connection,
                request_key,
            )

        if not saved:
            raise RuntimeError(
                "Impossibile registrare lo stato UNKNOWN"
            )

        return (
            summary
            + "\n⚠️ Esito della prenotazione incerto.\n"
            "Controlla Google Calendar prima di riprovare.\n"
            "Non verrà effettuato alcun tentativo automatico."
        )

    except Exception as error:
        print(
            "Errore aggiornamento stato prenotazione: "
            f"{type(error).__name__}: {error}"
        )

        return (
            summary
            + "\n⚠️ Non è stato possibile confermare "
            "lo stato della prenotazione.\n"
            "Verifica Google Calendar e PostgreSQL.\n"
            "Non ripetere automaticamente la richiesta."
        )


def register_ai_booking(
    request_key: str,
    postgres_password: str,
) -> RegistrationResult:
    """Registra una richiesta AI e assegna un ID Google univoco."""

    if not request_key or not request_key.strip():
        raise ValueError("request_key AI mancante o non valida")

    with get_connection(postgres_password) as connection:
        result = register_booking(connection, request_key)

        if result == RegistrationResult.CREATED:
            google_event_id = secrets.token_hex(16)

            assigned = assign_google_event_id(
                connection,
                request_key,
                google_event_id,
            )

            if not assigned:
                raise RuntimeError(
                    "Impossibile assegnare l'ID Google alla prenotazione"
                )

    return result


async def send_ai_result(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    intent,
    calendar_id: str,
    request_key: str,
    postgres_password: str,
) -> None:
    """Invia il risultato AI o richiede conferma in caso di conflitto."""

    message = update.effective_message

    if message is None:
        return

    # Le operazioni non di creazione seguono il flusso esistente.
    if intent.operazione != "crea":
        await message.reply_text(
            await format_ai_result(
                intent, calendar_id, request_key, postgres_password
            )
        )
        return

    # Non proporre nuove conferme per richieste già registrate.
    with get_connection(postgres_password) as connection:
        existing = get_booking_operation(connection, request_key)

    if existing is not None:
        await message.reply_text(
            await format_ai_result(
                intent, calendar_id, request_key, postgres_password
            )
        )
        return

    availability = await asyncio.to_thread(
        check_ai_availability,
        intent,
        calendar_id,
    )

    if availability != AvailabilityStatus.CONFLICT:
        await message.reply_text(
            await format_ai_result(
                intent, calendar_id, request_key, postgres_password
            )
        )
        return

    # La richiesta è occupata: attendiamo una conferma esplicita.
    confirmation_id = secrets.token_hex(8)

    pending = context.user_data.setdefault(
        "pending_ai_bookings", {}
    )

    pending[confirmation_id] = {
        "intent": intent,
        "request_key": request_key,
        "calendar_id": calendar_id,
    }

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Prenota comunque",
                callback_data=f"ai_confirm:{confirmation_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Annulla",
                callback_data=f"ai_cancel:{confirmation_id}",
            )
        ],
    ])

    await message.reply_text(
        format_intent_summary(intent)
        + "\n⚠️ L'orario richiesto è occupato.\n\n"
        "Vuoi creare comunque un appuntamento sovrapposto?",
        reply_markup=keyboard,
    )


async def handle_ai_booking_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    allowed_user_id: int,
    postgres_password: str,
) -> None:
    """Gestisce la conferma esplicita di una prenotazione AI."""

    query = update.callback_query
    user = update.effective_user

    if query is None:
        return

    if user is None or user.id != allowed_user_id:
        await query.answer("Non autorizzato.", show_alert=True)
        return

    await query.answer()

    try:
        action, confirmation_id = (query.data or "").split(":", 1)
    except ValueError:
        await query.edit_message_text("❌ Conferma non valida.")
        return

    async with conversation_locks:
        pending = context.user_data.setdefault(
            "pending_ai_bookings", {}
        )

        # La richiesta viene consumata una sola volta.
        booking = pending.pop(confirmation_id, None)

        if booking is None:
            await query.edit_message_text(
                "⚠️ Richiesta già gestita o non più disponibile."
            )
            return

        if action == "ai_cancel":
            await query.edit_message_text(
                "❌ Prenotazione annullata. Nessun evento creato."
            )
            return

        if action != "ai_confirm":
            await query.edit_message_text("❌ Operazione non valida.")
            return

        intent = booking["intent"]
        request_key = booking["request_key"]
        calendar_id = booking["calendar_id"]

        if not calendar_id or calendar_id == "primary":
            await query.edit_message_text(
                "❌ Calendario di test non valido."
            )
            return

        try:
            start = parse_ai_booking_start(intent)

            if not intent.cliente or not intent.cliente.strip():
                raise ValueError("Cliente mancante")

            registration = await asyncio.to_thread(
                register_ai_booking,
                request_key,
                postgres_password,
            )

            if registration == RegistrationResult.ALREADY_EXISTS:
                await query.edit_message_text(
                    "⚠️ Richiesta già registrata. "
                    "Nessuna nuova creazione eseguita."
                )
                return

            with get_connection(postgres_password) as connection:
                operation = get_booking_operation(
                    connection, request_key
                )

            if (
                operation is None
                or operation[0] != "PENDING"
                or not operation[1]
            ):
                raise RuntimeError(
                    "Operazione PostgreSQL non valida"
                )

            google_event_id = operation[1]

            # La conferma esplicita permette la sovrapposizione.
            result = await asyncio.to_thread(
                process_booking,
                calendar_id,
                f"Allenamento - {intent.cliente}",
                start,
                intent.durata_minuti,
                google_event_id,
                True,
            )

            if (
                result.status == BookingStatus.CREATED
                and result.event_id == google_event_id
            ):
                with get_connection(postgres_password) as connection:
                    saved = mark_booking_created(
                        connection,
                        request_key,
                        google_event_id,
                    )

                if not saved:
                    raise RuntimeError(
                        "Impossibile salvare lo stato CREATED"
                    )

                await query.edit_message_text(
                    "✅ Appuntamento creato su Google Calendar!\n\n"
                    f"Cliente: {intent.cliente}\n"
                    f"Data: {intent.data}\n"
                    f"Orario: {intent.ora}\n"
                    f"Google Event ID: {google_event_id}"
                )
                return

            # Qualsiasi risultato non confermato viene trattato
            # conservativamente come UNKNOWN.
            with get_connection(postgres_password) as connection:
                saved = mark_booking_unknown(
                    connection,
                    request_key,
                )

            if not saved:
                raise RuntimeError(
                    "Impossibile salvare lo stato UNKNOWN"
                )

            await query.edit_message_text(
                "⚠️ Esito della prenotazione incerto.\n"
                "Controlla Google Calendar prima di riprovare."
            )

        except Exception as error:
            print(
                "Errore conferma prenotazione AI: "
                f"{type(error).__name__}: {error}"
            )

            await query.edit_message_text(
                "⚠️ Prenotazione non confermata.\n"
                "Verifica Google Calendar e PostgreSQL.\n"
                "Nessun nuovo tentativo automatico."
            )