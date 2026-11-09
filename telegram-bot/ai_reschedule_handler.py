"""AI move conversation. Server-side plans, expiring single-use callbacks."""
import asyncio
from dataclasses import replace
import secrets
import time
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from ai.date_utils import normalize_relative_date
from ai.schemas import normalize_time
from ai_reschedule_service import candidates, prepare, execute, google_service, AvailabilityStatus, ROME
from reschedule_repository import RescheduleRepository
from runtime_guard import OWNER_REF

LOCK = asyncio.Lock()
TTL = 300
QUESTIONS = {
    'cliente': 'Quale cliente vuoi spostare?',
    'data_originale': 'In quale giorno si trova l’appuntamento attuale?',
    'data': 'Qual è la nuova data?',
    'ora': 'Qual è il nuovo orario? (HH:MM)',
}


def missing(intent):
    return next((field for field in QUESTIONS if getattr(intent, field) is None), None)


def keyboard(items):
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data)] for label,data in items])


def renew(session):
    session['token'] = secrets.token_hex(8)
    session['expires'] = time.monotonic()+TTL


def same_context(session, update, calendar_id):
    return (session['user_id'] == update.effective_user.id
            and session['chat_id'] == update.effective_chat.id
            and session['calendar_id'] == calendar_id and session['owner_ref'] == OWNER_REF
            and session['expires'] > time.monotonic())


async def begin(update, context, intent, request_key, calendar_id, password):
    async with LOCK:
        context.user_data.pop('ai_move', None)
        if intent.durata_minuti is not None:
            await update.effective_message.reply_text('M06.7 sposta soltanto data e ora. Non specificare una nuova durata; titolo e durata restano invariati.')
            return
        session = dict(intent=intent, request_key=request_key, user_id=update.effective_user.id,
                       chat_id=update.effective_chat.id, calendar_id=calendar_id, owner_ref=OWNER_REF,
                       stage='collect')
        renew(session)
        context.user_data['ai_move'] = session
        await advance(update, context, session, password)


async def advance(update, context, session, password):
    field = missing(session['intent'])
    if field:
        session['field'] = field
        await update.effective_message.reply_text(QUESTIONS[field])
        return
    try:
        repo = RescheduleRepository(password, session['calendar_id'], OWNER_REF)
        service = await asyncio.to_thread(google_service)
        found = await asyncio.to_thread(candidates, service, repo, session['intent'], session['calendar_id'], OWNER_REF)
        if not found:
            context.user_data.pop('ai_move', None)
            await update.effective_message.reply_text('Nessun appuntamento verificabile del prototipo corrisponde. Controlla cliente e collocazione originale e invia una nuova richiesta.')
            return
        if len(found) > 20:
            context.user_data.pop('ai_move', None)
            await update.effective_message.reply_text('Troppi risultati. Specifica anche l’orario originale in una nuova richiesta.')
            return
        if len(found) == 1:
            await show_plan(update.effective_message.reply_text, session, service, repo, found[0]['id'])
        else:
            session['stage'] = 'select'
            session['ids'] = [event['id'] for event in found]
            renew(session)
            choices = [(f"{event.get('summary','')} — {event['start']['dateTime']}", f"aimove:select:{session['token']}:{i}") for i,event in enumerate(found)]
            choices.append(('Annulla', f"aimove:cancel:{session['token']}:0"))
            await update.effective_message.reply_text('Quale appuntamento vuoi spostare?', reply_markup=keyboard(choices))
    except Exception:
        context.user_data.pop('ai_move', None)
        await update.effective_message.reply_text('Verifica non riuscita. Nessuna modifica avviata; richiesta interrotta.')


async def show_plan(send, session, service, repo, event_id):
    plan, status = await asyncio.to_thread(prepare, service, repo, session['intent'], event_id,
        session['request_key'], session['calendar_id'], OWNER_REF)
    if status not in (AvailabilityStatus.AVAILABLE, AvailabilityStatus.CONFLICT):
        raise ValueError('Availability unknown')
    session['plan'] = plan
    session['stage'] = 'confirm'
    renew(session)
    def fmt(value): return value.astimezone(ROME).strftime('%d/%m/%Y %H:%M')
    warning = '\n⚠️ Sovrapposizione rilevata: servirà una conferma aggiuntiva.' if status == AvailabilityStatus.CONFLICT else ''
    await send(f"Spostamento: {plan.title}\nAttuale: {fmt(plan.old_start)} → {fmt(plan.old_end)}\nNuovo: {fmt(plan.new_start)} → {fmt(plan.new_end)}\nTitolo e durata invariati.{warning}",
        reply_markup=keyboard([('Conferma spostamento', f"aimove:confirm:{session['token']}:0"), ('Annulla', f"aimove:cancel:{session['token']}:0")]))


async def handle_reply(update, context, calendar_id, password):
    """Return True if an existing move conversation consumed this message."""
    async with LOCK:
        session = context.user_data.get('ai_move')
        if not session:
            return False
        if not same_context(session, update, calendar_id):
            context.user_data.pop('ai_move',None)
            await update.effective_message.reply_text('Richiesta scaduta o contesto cambiato. Invia una nuova richiesta.')
            return True
        text = update.effective_message.text.strip()
        if text.casefold().rstrip('.!?') in ('annulla','annulla tutto','lascia perdere','fermati'):
            context.user_data.pop('ai_move',None)
            await update.effective_message.reply_text('Spostamento annullato. Nessuna modifica eseguita.')
            return True
        if session['stage'] != 'collect':
            await update.effective_message.reply_text('Usa i pulsanti della richiesta oppure scrivi “annulla”.')
            return True
        field = session['field']
        try:
            if field in ('data','data_originale'):
                value = normalize_relative_date(text)
                if value is None: raise ValueError()
            elif field == 'ora':
                value = normalize_time(text)
            else:
                if not text: raise ValueError()
                value = text
            session['intent'] = replace(session['intent'], **{field:value})
        except ValueError:
            await update.effective_message.reply_text(QUESTIONS[field])
            return True
        renew(session)
        await advance(update, context, session, password)
        return True


async def callback(update, context, allowed_user_id, calendar_id, password):
    query = update.callback_query
    if query is None or update.effective_user is None or update.effective_user.id != allowed_user_id:
        if query: await query.answer('Non autorizzato', show_alert=True)
        return
    async with LOCK:
        session = context.user_data.get('ai_move')
        parts = (query.data or '').split(':')
        if len(parts)!=4 or parts[0]!='aimove' or not session or not same_context(session,update,calendar_id) or parts[2]!=session['token']:
            await query.answer('Richiesta scaduta o già gestita', show_alert=True)
            return
        action=parts[1]
        if action == 'cancel':
            context.user_data.pop('ai_move',None)
            await query.answer()
            await query.edit_message_text('Spostamento annullato. Nessuna modifica eseguita.')
            return
        if (action,session['stage']) not in (('select','select'),('confirm','confirm'),('overlap','overlap')):
            await query.answer('Azione non valida',show_alert=True)
            return
        # Consume before any await/network operation. No replay on failure.
        context.user_data.pop('ai_move',None)
        await query.answer()
        try:
            repo=RescheduleRepository(password,calendar_id,OWNER_REF)
            service=await asyncio.to_thread(google_service)
            if action=='select':
                index=int(parts[3])
                if not 0 <= index < len(session['ids']): raise ValueError()
                await show_plan(query.edit_message_text,session,service,repo,session['ids'][index])
                context.user_data['ai_move']=session
                return
            result=await asyncio.to_thread(execute,service,repo,session['plan'],calendar_id,OWNER_REF,action=='overlap')
            if result=='CONFLICT':
                session['stage']='overlap'
                renew(session)
                context.user_data['ai_move']=session
                await query.edit_message_text('Conflitto confermato dal controllo attuale. Autorizzi esplicitamente la sovrapposizione?',reply_markup=keyboard([
                    ('Conferma sovrapposizione',f"aimove:overlap:{session['token']}:0"),
                    ('Annulla',f"aimove:cancel:{session['token']}:0")]))
                return
            messages={
                'UPDATED':'✅ Appuntamento spostato. Titolo e durata invariati.',
                'STALE':'Appuntamento cambiato nel frattempo. Nessuna modifica applicata; serve una nuova richiesta e conferma.',
                'UNAVAILABLE':'Disponibilità non verificabile. Nessuna modifica avviata.',
                'DUPLICATE_OR_UNRESOLVED':'Richiesta già registrata o precedente modifica irrisolta. Nessun nuovo tentativo.',
                'REJECTED':'Appartenenza o contesto non verificabili. Nessuna modifica avviata.',
                'UNKNOWN':'Esito incerto. Verificare Google e PostgreSQL; non ripetere automaticamente.',
            }
            await query.edit_message_text(messages[result])
        except Exception:
            context.user_data.pop('ai_move',None)
            await query.edit_message_text('Operazione interrotta o non confermata. Verificare Google e PostgreSQL prima di riprovare. Nessun tentativo automatico.')
