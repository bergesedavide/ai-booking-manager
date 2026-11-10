"""Client-only conversation. Called under ai_handler.conversation_locks.

No Google imports/calls. Authorization is repeated before accessing any state.
Sessions are local to the user/chat and are lost on restart, like other AI plans.
"""
from dataclasses import dataclass
import re
import secrets
import time

from ai.client_interpreter import ClientInterpreter
from ai.client_schemas import ClientIntent, ClientResponseError, UnsupportedClientIntent, validate_client_intent
from ai.ollama_client import OllamaError, OllamaErrorKind
from client_service import ClientService, ClientStatus
from telegram_delivery import deliver
from worker_guard import await_worker
import asyncio

TTL = 300
interpreter = ClientInterpreter()


@dataclass
class ClientSession:
    user_id: int
    chat_id: int
    request_key: str
    intent: ClientIntent
    stage: str
    expires: float
    token: str
    last_message_id: int
    prompt_message_id: int = 0


def is_client_request(text: str) -> bool:
    text = text.strip().casefold()
    if not re.match(r'^(aggiungi|registra|inserisci|crea)\b', text):
        return False
    if re.search(r'\bclient[ei]\b', text):
        return True
    # Narrow exception for the approved "Registra Nome Cognome, telefono ...".
    return bool(re.match(r'^registra\s+[^,]+,\s*telefono\b', text)
                and not re.search(r'\b(appuntamento|allenamento|prenota|sposta|cancella)\b', text))


def _new_procedure(text: str) -> bool:
    return (is_client_request(text) or text == 'confermo omonimo'
            or bool(re.match(r'^\s*(crea|inserisci|aggiungi|registra)\b', text.casefold()))
            or bool(re.search(
                r'\b(prenota|prenotare|prenotazione|sposta|spostare|modifica|modificare|cancella|cancellare|elimina|eliminare|appuntament[oi]|allenament[oi])\b',
                text.casefold())))


def _discard(sessions, session):
    if sessions.get(session.chat_id) is session:
        sessions.pop(session.chat_id, None)


async def _prompt(update, sessions, session, text):
    async def send(*args, **kwargs):
        sent = await update.effective_message.reply_text(*args, **kwargs)
        message_id = getattr(sent, 'message_id', None)
        if type(message_id) is int:
            session.prompt_message_id = message_id
    try:
        delivered = await deliver(send, text)
    except asyncio.CancelledError:
        _discard(sessions, session)
        raise
    if not delivered:
        _discard(sessions, session)


async def _save(update, service, sessions, receipts, session, allow_homonym=False):
    # Consume confirmation before any await; a failed/cancelled attempt cannot
    # restore it. Keep the original delivery identity for all stages.
    session.stage = 'working'
    try:
        result = await await_worker(
            service.add_client, authorized_telegram_user_id=session.user_id,
            request_key=session.request_key, first_name=session.intent.first_name,
            last_name=session.intent.last_name, phone=session.intent.phone,
            email=session.intent.email, notes=session.intent.notes,
            allow_homonym=allow_homonym)
    except asyncio.CancelledError:
        session.stage = 'uncertain'
        receipts[session.request_key] = ClientStatus.UNKNOWN
        raise
    except Exception:
        session.stage = 'uncertain'
        receipts[session.request_key] = ClientStatus.UNKNOWN
        await deliver(update.effective_message.reply_text,
                      'Esito inserimento non confermato. Non ripetere la richiesta; serve una verifica.')
        return
    if result.status is ClientStatus.HOMONYM_REQUIRES_CONFIRMATION:
        session.stage = 'confirm'
        session.expires = time.monotonic() + TTL
        session.token = secrets.token_hex(8)
        await _prompt(update, sessions, session,
            'Esiste già un cliente con questo nome e cognome. Vuoi aggiungerne un altro? '
            'Scrivi esattamente "confermo omonimo" oppure "annulla".')
        return
    if result.status in (ClientStatus.UNKNOWN, ClientStatus.UNRESOLVED):
        session.stage = 'uncertain'
        receipts[session.request_key] = ClientStatus.UNKNOWN
        text = 'Esito inserimento non confermato. Non ripetere la richiesta; serve una verifica.'
    else:
        _discard(sessions, session)
        text = {
            ClientStatus.CREATED: f'Cliente aggiunto. ID: {result.client_id}.',
            ClientStatus.ALREADY_EXISTS: f'Richiesta già registrata. ID cliente: {result.client_id}.',
            ClientStatus.REQUEST_CONFLICT: 'Richiesta già utilizzata con dati diversi. Nessun dato sovrascritto.',
            ClientStatus.UNAVAILABLE: 'Gestione clienti non disponibile. Nessun tentativo automatico.',
        }.get(result.status, 'Esito non confermato. Non ripetere la richiesta; serve una verifica.')
    # Delivery failure must not change the committed outcome or repeat the call.
    await deliver(update.effective_message.reply_text, text)


async def _advance(update, service, sessions, receipts, session):
    missing = session.intent.missing_fields
    if missing:
        session.stage = 'collect'
        await _prompt(update, sessions, session,
                      'Qual è il nome del cliente?' if missing[0] == 'first_name'
                      else 'Qual è il cognome del cliente?')
    else:
        await _save(update, service, sessions, receipts, session)


async def handle_message(update, context, allowed_user_id, password, *, appointment_pending=False):
    """Return whether this message belongs to the client path; never fall back on error."""
    user, message = update.effective_user, update.effective_message
    if user is None or message is None or not message.text or user.id != allowed_user_id:
        return False
    text = message.text.strip()
    sessions = context.user_data.get('ai_clients', {})
    session = sessions.get(message.chat_id)
    explicit = is_client_request(text)
    if session is None and not explicit:
        if text == 'confermo omonimo':
            await deliver(message.reply_text, 'Nessuna conferma clienti attiva. Nessun inserimento eseguito.')
            return True
        return False
    if appointment_pending:
        await deliver(message.reply_text, 'Hai una procedura appuntamenti pendente. Scrivi "annulla" prima di aggiungere clienti.')
        return True
    sessions = context.user_data.setdefault('ai_clients', {})
    receipts = context.user_data.setdefault('ai_client_uncertain', {})
    if session is not None:
        if (session.user_id, session.chat_id) != (user.id, message.chat_id):
            await deliver(message.reply_text, 'Contesto clienti non valido. Nessuna operazione eseguita.')
            return True
        if text.casefold() == 'annulla':
            _discard(sessions, session)
            await deliver(message.reply_text, 'Inserimento clienti annullato. I clienti già registrati restano invariati.')
            return True
        if session.expires <= time.monotonic():
            _discard(sessions, session)
            await deliver(message.reply_text, 'Procedura clienti scaduta. Eventuali esiti incerti richiedono verifica; nessun nuovo inserimento eseguito.')
            return True
        if session.stage == 'uncertain':
            await deliver(message.reply_text, 'Esito precedente non confermato. Non ripetere l’inserimento; serve una verifica. Scrivi "annulla" per chiudere la procedura.')
            return True
        if message.message_id <= max(session.last_message_id, session.prompt_message_id):
            await deliver(message.reply_text, 'Messaggio già gestito o precedente alla domanda. Nessun nuovo tentativo.')
            return True
        if _new_procedure(text) and not (session.stage == 'confirm' and text == 'confermo omonimo'):
            await deliver(message.reply_text, 'Hai un inserimento clienti pendente. Scrivi "annulla" prima di avviare un’altra procedura.')
            return True
        session.last_message_id = message.message_id
        service = ClientService(password)
        if session.stage == 'confirm':
            if text != 'confermo omonimo':
                await _prompt(update, sessions, session, 'Scrivi esattamente "confermo omonimo" oppure "annulla".')
            else:
                await _save(update, service, sessions, receipts, session, allow_homonym=True)
            return True
        if session.stage == 'collect':
            field = session.intent.missing_fields[0]
            try:
                data = {name: getattr(session.intent, name) for name in
                        ('operation', 'first_name', 'last_name', 'phone', 'email', 'notes')}
                data[field] = text
                session.intent = validate_client_intent(data)
            except ClientResponseError:
                await _prompt(update, sessions, session, 'Dato non valido. Rispondi soltanto con il ' + ('nome' if field == 'first_name' else 'cognome') + ' richiesto oppure scrivi "annulla".')
                return True
            session.expires = time.monotonic() + TTL
            await _advance(update, service, sessions, receipts, session)
        return True
    request_key = f'client:{message.chat_id}:{message.message_id}'
    if request_key in receipts:
        await deliver(message.reply_text, 'Questa richiesta ha un esito non confermato. Non verrà ripetuta.')
        return True
    if re.search(r'\b(prenota|sposta|modifica|cancella|elimina|appuntament[oi])\b', text.casefold()):
        await deliver(message.reply_text, 'Invia separatamente l’inserimento cliente e la richiesta sull’appuntamento.')
        return True
    service = ClientService(password)
    try:
        if not await await_worker(service.schema_available):
            await deliver(message.reply_text, 'Gestione clienti non ancora disponibile. Nessuna operazione avviata.')
            return True
        receipt = await await_worker(service.lookup_request,
            authorized_telegram_user_id=user.id, request_key=request_key)
        if receipt.status is ClientStatus.ALREADY_EXISTS:
            await deliver(message.reply_text, f'Richiesta già registrata. ID cliente: {receipt.client_id}.')
            return True
        if receipt.status in (ClientStatus.UNRESOLVED, ClientStatus.UNKNOWN):
            receipts[request_key] = receipt.status
            await deliver(message.reply_text, 'Tentativo precedente non risolto. Non verrà ripetuto; serve una verifica.')
            return True
        if receipt.status is not ClientStatus.NOT_FOUND:
            await deliver(message.reply_text, 'Impossibile verificare i tentativi precedenti. Nessun inserimento avviato.')
            return True
        intent = await await_worker(interpreter.interpret, text)
    except asyncio.CancelledError:
        raise
    except UnsupportedClientIntent:
        await deliver(message.reply_text, 'Richiesta clienti non riconosciuta. Nessuna operazione avviata.')
        return True
    except ClientResponseError:
        await deliver(message.reply_text, 'Non sono riuscito a interpretare i dati del cliente. Riformula la richiesta.')
        return True
    except OllamaError as error:
        text = {
            OllamaErrorKind.TIMEOUT: 'L’assistente sta impiegando troppo tempo. Nessun inserimento avviato.',
            OllamaErrorKind.CONNECTION: 'L’assistente non è disponibile. Nessun inserimento avviato.',
            OllamaErrorKind.HTTP: 'L’assistente non riesce a rispondere. Nessun inserimento avviato.',
            OllamaErrorKind.INVALID_RESPONSE: 'Risposta dell’assistente non valida. Nessun inserimento avviato.',
        }[error.kind]
        await deliver(message.reply_text, text)
        return True
    except Exception:
        await deliver(message.reply_text, 'Impossibile preparare l’inserimento cliente. Nessun tentativo automatico.')
        return True
    session = ClientSession(user.id, message.chat_id, request_key, intent, 'collect',
                            time.monotonic()+TTL, secrets.token_hex(8), message.message_id)
    sessions[message.chat_id] = session
    await _advance(update, service, sessions, receipts, session)
    return True
