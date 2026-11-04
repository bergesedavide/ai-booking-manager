"""Integrazione dell'agente AI con Telegram.

M06.3.1: interpretazione e riepilogo, senza operazioni su Google Calendar.
"""

import asyncio
from pathlib import Path
import sys
import secrets

from telegram import Update
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
)
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

                await message.reply_text(
                    await format_ai_result(intent, calendar_id, manager.request_key)
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

            await message.reply_text(
                await format_ai_result(intent, calendar_id, manager.request_key)
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

async def format_ai_result(intent, calendar_id: str, request_key: str) -> str:
    """Genera il riepilogo AI e verifica la disponibilità per nuove prenotazioni."""

    if not request_key or not request_key.strip():
        raise ValueError("request_key AI mancante o non valida")

    summary = format_intent_summary(intent)

    # M06.4 verifica soltanto le richieste di creazione.
    if intent.operazione != "crea":
        return summary

    status = await asyncio.to_thread(
        check_ai_availability,
        intent,
        calendar_id,
    )

    if status == AvailabilityStatus.AVAILABLE:
        result = "✅ L'orario richiesto è disponibile."
    elif status == AvailabilityStatus.CONFLICT:
        result = "❌ L'orario richiesto è occupato."
    else:
        result = (
            "⚠️ Non è stato possibile verificare la disponibilità.\n"
            "L'orario non può essere considerato disponibile."
        )

    return f"{summary}\n\n📅 Google Calendar\n{result}"


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