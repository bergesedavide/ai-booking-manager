"""Integrazione dell'agente AI con Telegram.

M06.3.1: interpretazione e riepilogo, senza operazioni su Google Calendar.
"""

import asyncio
from pathlib import Path
import sys
import secrets
import time
from dataclasses import dataclass, replace


from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai.schemas import AppointmentIntent
from ai.conversation import ConversationManager
from ai.interpreter import AppointmentInterpreter, ModelResponseError, UnknownIntentError
from ai.ollama_client import OllamaClient, OllamaError, OllamaErrorKind
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
from worker_guard import await_worker
from telegram_delivery import deliver as deliver_creation_message
from runtime_guard import OWNER_REF


interpreter = AppointmentInterpreter(OllamaClient())

conversations: dict[tuple[int, int], ConversationManager] = {}
conversation_locks = asyncio.Lock()

async def infer_or_report(send, conversation_key, function, *args):
    """Classify only failures from the inference phase, never booking failures."""
    try:
        return True, await await_worker(function, *args)
    except asyncio.CancelledError:
        conversations.pop(conversation_key, None)
        raise
    except (OllamaError, ModelResponseError) as error:
        conversations.pop(conversation_key, None)
        if isinstance(error, OllamaError):
            category = error.kind.value
            text = {
                OllamaErrorKind.CONNECTION: "L'assistente non è disponibile. Nessuna operazione avviata.",
                OllamaErrorKind.TIMEOUT: "L'assistente sta impiegando troppo tempo. Nessuna operazione avviata.",
                OllamaErrorKind.HTTP: "L'assistente non riesce a rispondere. Nessuna operazione avviata.",
                OllamaErrorKind.INVALID_RESPONSE: "Non sono riuscito a interpretare la richiesta. Prova a riformularla.",
            }[error.kind]
        elif isinstance(error, UnknownIntentError):
            category = "unknown_intent"
            text = "Non ho riconosciuto l'operazione. Specifica se vuoi prenotare, spostare o cancellare."
        else:
            category = "invalid_response"
            text = "Non sono riuscito a interpretare la richiesta. Prova a riformularla."
        print(f"Errore inferenza AI: {category}")
        await deliver_creation_message(send, text)
        return False, None


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

    from ai_deletion_handler import handle_reply as handle_delete_reply
    if await handle_delete_reply(update, context, calendar_id, postgres_password):
        return

    from ai_reschedule_handler import handle_reply as handle_move_reply
    if await handle_move_reply(update, context, calendar_id, postgres_password):
        return

    if await handle_pending_creation_reply(update, context):
        return

    conversation_key = (user.id, message.chat_id)

    request_key = f"ai:{message.chat_id}:{message.message_id}"

    async with conversation_locks:
        try:
            manager = conversations.get(conversation_key)

            # Se esiste una conversazione, gestiamo la risposta.
            if manager is not None and manager.current_intent is not None:

                ok, handled = await infer_or_report(
                    message.reply_text, conversation_key, manager.handle_reply,
                    message.text,
                    interpreter,
                    request_key,
                )

                if not ok:
                    return

                # L'utente ha annullato la conversazione.
                if manager.current_intent is None:
                    conversations.pop(conversation_key, None)
                    await deliver_creation_message(message.reply_text, "❌ Conversazione annullata.")
                    return

                if not handled:
                    if not await deliver_creation_message(message.reply_text,
                        "⚠️ Non ho capito la risposta.\n"
                        f"{manager.next_question() or 'Riformula la richiesta.'}"):
                        conversations.pop(conversation_key, None)
                    return

                question = manager.next_question()

                if question is not None:
                    if not await deliver_creation_message(message.reply_text, question):
                        conversations.pop(conversation_key, None)
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
                conversations.pop(conversation_key, None)
                return

            # Nessuna conversazione attiva: interpretiamo il messaggio.
            ok, intent = await infer_or_report(
                message.reply_text, conversation_key, interpreter.interpret, message.text,
            )
            if not ok:
                return

            manager = ConversationManager()
            manager.start(intent, request_key)
            conversations[conversation_key] = manager

            question = manager.next_question()

            if question is not None:
                if not await deliver_creation_message(message.reply_text, question):
                    conversations.pop(conversation_key, None)
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
            conversations.pop(conversation_key, None)

        except Exception as error:
            print(f"Errore agente AI: {type(error).__name__}")

            # Elimina eventuale stato conversazionale incompleto.
            conversations.pop(conversation_key, None)

            await deliver_creation_message(
                message.reply_text,
                "⚠️ Impossibile preparare la richiesta. Nessun tentativo automatico. "
                "Se una precedente operazione è incerta, verificarne prima l’esito.",
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

async def _execute_confirmed_booking(
    intent,
    calendar_id: str,
    request_key: str,
    postgres_password: str,
    allow_conflict: bool = False,
) -> str | None:
    """Execute a consumed plan; None requests overlap consent before registration."""

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
        existing = await asyncio.to_thread(read_booking, postgres_password, request_key)

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
            f"{type(error).__name__}"
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

    if availability == AvailabilityStatus.CONFLICT and not allow_conflict:
        return None  # No PENDING yet: require a fresh overlap token.

    if availability not in (AvailabilityStatus.AVAILABLE, AvailabilityStatus.CONFLICT):
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
        registration = await await_worker(
            register_ai_booking,
            request_key,
            postgres_password,
            calendar_id,
        )

        if registration == RegistrationResult.ALREADY_EXISTS:
            return (
                summary
                + "\n⚠️ Richiesta già registrata. "
                "Nessuna nuova prenotazione eseguita."
            )

        operation = await asyncio.to_thread(read_booking, postgres_password, request_key)

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
            f"{type(error).__name__}"
        )

        return (
            summary
            + "\n⚠️ Impossibile preparare la prenotazione. "
            "Nessuna scrittura Google avviata."
        )

    # 5. Creazione Google Calendar.
    # Da questo punto l'esito potrebbe diventare incerto.
    try:
        result = await await_worker(
            process_booking,
            calendar_id,
            f"Allenamento - {intent.cliente}",
            start,
            intent.durata_minuti,
            google_event_id,
            force_booking=allow_conflict,
            before_google_write=lambda: record_google_write_start(
                postgres_password, request_key, google_event_id, calendar_id
            ),
        )

    except Exception as error:
        print(
            "Errore chiamata servizio prenotazione: "
            f"{type(error).__name__}"
        )

        result = None

    # 6. Aggiornamento dello stato PostgreSQL.
    try:
        if (
            result is not None
            and result.status == BookingStatus.CREATED
            and result.event_id == google_event_id
        ):
            saved = await await_worker(save_booking, postgres_password, mark_booking_created, request_key, google_event_id)

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
            saved = await await_worker(save_booking, postgres_password, mark_booking_failed, request_key)

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
        saved = await await_worker(save_booking, postgres_password, mark_booking_unknown, request_key)

        if not saved:
            raise RuntimeError(
                "Impossibile registrare lo stato UNKNOWN"
            )

        return (
            summary
            + "\n⚠️ Esito della prenotazione incerto.\n"
            "Verifica Google Calendar e PostgreSQL senza ripetere la prenotazione.\n"
            "Non verrà effettuato alcun tentativo automatico."
        )

    except Exception as error:
        print(
            "Errore aggiornamento stato prenotazione: "
            f"{type(error).__name__}"
        )

        return (
            summary
            + "\n⚠️ Non è stato possibile confermare "
            "lo stato della prenotazione.\n"
            "Verifica Google Calendar e PostgreSQL.\n"
            "Non ripetere automaticamente la richiesta."
        )


def read_booking(password, request_key):
    """Own the complete read transaction in the calling worker."""
    with get_connection(password) as connection:
        return get_booking_operation(connection, request_key)


def save_booking(password, update, *args):
    """Return only after the outcome transaction has committed and closed."""
    with get_connection(password) as connection:
        return update(connection, *args)


def register_ai_booking(
    request_key: str,
    postgres_password: str,
    calendar_id: str,
) -> RegistrationResult:
    """Registra una richiesta AI e assegna un ID Google univoco."""

    if not request_key or not request_key.strip():
        raise ValueError("request_key AI mancante o non valida")

    if not calendar_id or calendar_id == "primary":
        raise ValueError("Calendario non valido")

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

            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE booking_operations SET calendar_id=%s, owner_ref=%s, "
                    "execution_version=2 WHERE request_key=%s AND status='PENDING'",
                    (calendar_id, OWNER_REF, request_key),
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

    if intent.operazione == "cancella":
        from ai_deletion_handler import begin
        await begin(update, context, intent, request_key, calendar_id, postgres_password)
        return

    if intent.operazione == "modifica":
        from ai_reschedule_handler import begin
        await begin(update, context, intent, request_key, calendar_id, postgres_password)
        return

    # Le operazioni non di creazione seguono il flusso esistente.
    if intent.operazione != "crea":
        await deliver_creation_message(message.reply_text,
            await format_ai_result(
                intent, calendar_id, request_key, postgres_password
            )
        )
        return

    await prepare_creation(update, context, intent, calendar_id, request_key, postgres_password)


@dataclass(frozen=True)
class CreationPlan:
    intent: AppointmentIntent
    request_key: str
    user_id: int
    chat_id: int
    calendar_id: str
    owner_ref: str
    token: str
    expires: float
    phase: str = "confirm"



def invalidate_creation(context, user_id, chat_id):
    pending = context.user_data.setdefault("pending_ai_bookings", {})
    for token, plan in list(pending.items()):
        if isinstance(plan, CreationPlan) and (plan.user_id, plan.chat_id) == (user_id, chat_id):
            pending.pop(token, None)


async def handle_pending_creation_reply(update, context):
    """Cancel a draft, or invalidate its buttons before interpreting a new plan."""
    async with conversation_locks:
        user_id, chat_id = update.effective_user.id, update.effective_message.chat_id
        pending = context.user_data.get("pending_ai_bookings", {})
        plans = [p for p in pending.values() if isinstance(p, CreationPlan)
                 and (p.user_id, p.chat_id) == (user_id, chat_id)]
        text = update.effective_message.text.strip().casefold().rstrip(".!?")
        if not plans:
            manager = conversations.get((user_id, chat_id))
            if (text == "annulla prenotazione" and manager is not None
                    and manager.current_intent is not None and manager.current_intent.operazione == "crea"):
                manager.reset()
                conversations.pop((user_id, chat_id), None)
                await deliver_creation_message(update.effective_message.reply_text,
                    "Prenotazione in preparazione annullata. Nessun appuntamento esistente cancellato.")
                return True
            return False
        invalidate_creation(context, user_id, chat_id)
        if text in ("annulla", "annulla tutto", "annulla prenotazione", "lascia perdere", "fermati"):
            await deliver_creation_message(update.effective_message.reply_text,
                "Prenotazione in preparazione annullata. Nessun appuntamento esistente cancellato.")
            return True
        # Any new text makes the old plan unusable. The existing interpreter then
        # handles the new message, including an explicit request to delete an event.
        return False


async def format_ai_result(intent, calendar_id, request_key, postgres_password):
    """Read-only view of an existing request. Never creates a booking."""
    summary = format_intent_summary(intent)
    if intent.operazione != "crea":
        return summary
    existing = await asyncio.to_thread(read_booking, postgres_password, request_key)
    if existing is None:
        return summary + "Conferma esplicita necessaria. Nessuna creazione avviata."
    status, event_id = existing
    detail = f" Evento Google: {event_id}." if status == "CREATED" and event_id else ""
    return summary + f"Richiesta già registrata: {status}.{detail} Nessuna nuova creazione eseguita."


def creation_keyboard(plan):
    action = "overlap" if plan.phase == "overlap" else "confirm"
    label = "Conferma sovrapposizione" if action == "overlap" else "Conferma"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f"ai_{action}:{plan.token}")],
        [InlineKeyboardButton("Annulla", callback_data=f"ai_cancel:{plan.token}")],
    ])


async def prepare_creation(update, context, intent, calendar_id, request_key, postgres_password):
    user_id, chat_id = update.effective_user.id, update.effective_message.chat_id
    invalidate_creation(context, user_id, chat_id)
    try:
        if not calendar_id or calendar_id.strip().lower() == "primary" or not request_key:
            raise ValueError("Contesto non valido")
        existing = await asyncio.to_thread(read_booking, postgres_password, request_key)
        if existing is not None:
            result = await format_ai_result(intent, calendar_id, request_key, postgres_password)
            await deliver_creation_message(update.effective_message.reply_text, result)
            return
        parse_ai_booking_start(intent)
        if not intent.cliente or not intent.cliente.strip():
            raise ValueError("Cliente mancante")
        status = await asyncio.to_thread(check_ai_availability, intent, calendar_id)
        if status not in (AvailabilityStatus.AVAILABLE, AvailabilityStatus.CONFLICT):
            await deliver_creation_message(update.effective_message.reply_text,
                "Disponibilità non verificabile. Nessuna creazione avviata.")
            return
        plan = CreationPlan(intent, request_key, user_id, chat_id, calendar_id, OWNER_REF,
                            secrets.token_hex(8), time.monotonic() + 300)
        context.user_data.setdefault("pending_ai_bookings", {})[plan.token] = plan
        warning = "\n⚠️ Orario occupato: servirà una seconda conferma della sovrapposizione." if status == AvailabilityStatus.CONFLICT else ""
        delivered = await deliver_creation_message(update.effective_message.reply_text,
            format_intent_summary(intent) + "Vuoi creare questo appuntamento?" + warning,
            reply_markup=creation_keyboard(plan))
        if not delivered:
            context.user_data["pending_ai_bookings"].pop(plan.token, None)
    except Exception:
        invalidate_creation(context, user_id, chat_id)
        await deliver_creation_message(update.effective_message.reply_text,
            "Impossibile preparare la prenotazione. Nessuna scrittura Google avviata.")


async def handle_ai_booking_callback(update, context, allowed_user_id, postgres_password, calendar_id):
    query, user = update.callback_query, update.effective_user
    if query is None:
        return
    if user is None or user.id != allowed_user_id:
        await deliver_creation_message(query.answer, "Non autorizzato", show_alert=True)
        return
    async with conversation_locks:
        parts = (query.data or "").split(":")
        pending = context.user_data.setdefault("pending_ai_bookings", {})
        plan = pending.get(parts[1]) if len(parts) == 2 else None
        action = parts[0]
        valid = (isinstance(plan, CreationPlan) and plan.token == parts[1]
                 and plan.user_id == user.id and update.effective_chat is not None
                 and plan.chat_id == update.effective_chat.id
                 and plan.calendar_id == calendar_id and bool(calendar_id)
                 and calendar_id.strip().lower() != "primary" and plan.owner_ref == OWNER_REF
                 and plan.expires > time.monotonic()
                 and (action == "ai_cancel" or (action, plan.phase) in
                      (("ai_confirm", "confirm"), ("ai_overlap", "overlap"))))
        if not valid:
            await deliver_creation_message(query.answer,
                "Conferma scaduta, già gestita o contesto non valido", show_alert=True)
            return
        pending.pop(plan.token, None)  # Consume before external I/O.
        await deliver_creation_message(query.answer, "")
        if action == "ai_cancel":
            await deliver_creation_message(query.edit_message_text,
                "Prenotazione in preparazione annullata. Nessun appuntamento esistente cancellato.")
            return
        try:
            existing = await asyncio.to_thread(read_booking, postgres_password, plan.request_key)
            if existing is not None:
                result = await format_ai_result(plan.intent, calendar_id, plan.request_key, postgres_password)
            else:
                status = await asyncio.to_thread(check_ai_availability, plan.intent, calendar_id)
                if status not in (AvailabilityStatus.AVAILABLE, AvailabilityStatus.CONFLICT):
                    result = "Disponibilità non verificabile. Nessuna creazione avviata."
                else:
                    if status == AvailabilityStatus.CONFLICT and plan.phase == "confirm":
                        result = None
                    else:
                        result = await _execute_confirmed_booking(plan.intent, calendar_id,
                            plan.request_key, postgres_password, allow_conflict=plan.phase == "overlap")
                    if result is None:
                        overlap = replace(plan, token=secrets.token_hex(8), expires=time.monotonic()+300, phase="overlap")
                        pending[overlap.token] = overlap
                        delivered = await deliver_creation_message(query.edit_message_text,
                            format_intent_summary(plan.intent) + "⚠️ Sovrapposizione rilevata. Autorizzi esplicitamente la creazione sovrapposta?",
                            reply_markup=creation_keyboard(overlap))
                        if not delivered:
                            pending.pop(overlap.token, None)
                        return
        except Exception:
            result = ("Esito della prenotazione non confermato. Verificare Google Calendar e PostgreSQL; "
                      "non ripetere la richiesta. Nessun tentativo automatico.")
        # Delivery happens after all operation-state decisions, outside their error handler.
        await deliver_creation_message(query.edit_message_text, result)


def record_google_write_start(password, request_key, event_id, calendar_id):
    """Commit the write boundary; failure prevents any Google insert."""
    with get_connection(password) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE booking_operations SET google_write_started_at=NOW(), updated_at=NOW() "
                "WHERE request_key=%s AND google_event_id=%s AND calendar_id=%s "
                "AND owner_ref=%s AND execution_version=2 AND status='PENDING' "
                "AND google_write_started_at IS NULL RETURNING id",
                (request_key, event_id, calendar_id, OWNER_REF),
            )
            if cursor.fetchone() is None:
                raise RuntimeError("Avvio scrittura non autorizzato")
