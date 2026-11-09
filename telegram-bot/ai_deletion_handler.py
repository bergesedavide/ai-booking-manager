"""Expiring AI deletion conversation, separate from traditional /cancella."""
import asyncio
from dataclasses import replace
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from ai.date_utils import normalize_relative_date
from ai_reschedule_handler import renew, same_context, ROME
from ai_reschedule_service import google_service
from ai_deletion_service import candidates, prepare, execute
from deletion_repository import DeletionRepository
from runtime_guard import OWNER_REF

LOCK = asyncio.Lock()
QUESTIONS = {'cliente':'Quale cliente?', 'data':'In quale giorno si trova l’appuntamento da cancellare?'}


def keyboard(items):
    return InlineKeyboardMarkup([[InlineKeyboardButton(label,callback_data=data)] for label,data in items])


def missing(intent):
    return next((key for key in QUESTIONS if getattr(intent,key) is None),None)


async def begin(update,context,intent,request_key,calendar_id,password):
    async with LOCK:
        context.user_data.pop('ai_delete',None)
        session=dict(intent=intent,request_key=request_key,user_id=update.effective_user.id,
            chat_id=update.effective_chat.id,calendar_id=calendar_id,owner_ref=OWNER_REF,stage='collect')
        renew(session)
        context.user_data['ai_delete']=session
        await advance(update,context,session,password)


async def advance(update,context,session,password):
    field=missing(session['intent'])
    if field:
        session['field']=field
        await update.effective_message.reply_text(QUESTIONS[field])
        return
    try:
        repo=DeletionRepository(password,session['calendar_id'],OWNER_REF)
        service=await asyncio.to_thread(google_service)
        found=await asyncio.to_thread(candidates,service,repo,session['intent'],session['calendar_id'],OWNER_REF)
        if not found or len(found)>20:
            context.user_data.pop('ai_delete',None)
            await update.effective_message.reply_text('Nessun risultato cancellabile o ricerca troppo ampia. Controlla cliente, data e ora; eventuali operazioni irrisolte richiedono verifica manuale.')
            return
        if len(found)==1:
            await show_plan(update.effective_message.reply_text,session,service,repo,found[0]['id'])
        else:
            session['stage']='select'
            session['ids']=[event['id'] for event in found]
            renew(session)
            choices=[(f"{event.get('summary','')} — {event['start']['dateTime']}",f"aidelete:select:{session['token']}:{i}") for i,event in enumerate(found)]
            choices.append(('Annulla',f"aidelete:cancel:{session['token']}:0"))
            await update.effective_message.reply_text('Quale appuntamento vuoi cancellare?',reply_markup=keyboard(choices))
    except Exception:
        context.user_data.pop('ai_delete',None)
        await update.effective_message.reply_text('Verifica non riuscita. Nessuna cancellazione avviata.')


async def show_plan(send,session,service,repo,event_id):
    plan=await asyncio.to_thread(prepare,service,repo,session['intent'],event_id,session['request_key'],session['calendar_id'],OWNER_REF)
    session['plan']=plan
    session['stage']='confirm'
    renew(session)
    def fmt(value):return value.astimezone(ROME).strftime('%d/%m/%Y %H:%M')
    await send(f"Cancellazione: {plan.title}\n{fmt(plan.old_start)} → {fmt(plan.old_end)}\nVuoi eliminare definitivamente questo appuntamento?",reply_markup=keyboard([
        ('Conferma cancellazione',f"aidelete:confirm:{session['token']}:0"),
        ('Annulla',f"aidelete:cancel:{session['token']}:0")]))


async def handle_reply(update,context,calendar_id,password):
    async with LOCK:
        session=context.user_data.get('ai_delete')
        if not session:return False
        if not same_context(session,update,calendar_id):
            context.user_data.pop('ai_delete',None)
            await update.effective_message.reply_text('Richiesta scaduta o contesto cambiato. Invia una nuova richiesta.')
            return True
        text=update.effective_message.text.strip()
        if text.casefold().rstrip('.!?') in ('annulla','annulla tutto','lascia perdere','fermati'):
            context.user_data.pop('ai_delete',None)
            await update.effective_message.reply_text('Cancellazione annullata. Nessun evento eliminato.')
            return True
        if session['stage']!='collect':
            await update.effective_message.reply_text('Usa i pulsanti della richiesta oppure scrivi “annulla”.')
            return True
        field=session['field']
        try:
            value=normalize_relative_date(text) if field=='data' else text
            if not value:raise ValueError()
            session['intent']=replace(session['intent'],**{field:value})
        except ValueError:
            await update.effective_message.reply_text(QUESTIONS[field])
            return True
        renew(session)
        await advance(update,context,session,password)
        return True


async def callback(update,context,allowed_user_id,calendar_id,password):
    query=update.callback_query
    if query is None or update.effective_user is None or update.effective_user.id!=allowed_user_id:
        if query:await query.answer('Non autorizzato',show_alert=True)
        return
    async with LOCK:
        session=context.user_data.get('ai_delete')
        parts=(query.data or '').split(':')
        if len(parts)!=4 or parts[0]!='aidelete' or not session or not same_context(session,update,calendar_id) or parts[2]!=session['token']:
            await query.answer('Richiesta scaduta o già gestita',show_alert=True)
            return
        action=parts[1]
        if action=='cancel':
            context.user_data.pop('ai_delete',None)
            await query.answer()
            await query.edit_message_text('Cancellazione annullata. Nessun evento eliminato.')
            return
        if (action,session['stage']) not in (('select','select'),('confirm','confirm')):
            await query.answer('Azione non valida',show_alert=True)
            return
        # Consume before any network operation; failed callbacks cannot replay.
        context.user_data.pop('ai_delete',None)
        await query.answer()
        try:
            repo=DeletionRepository(password,calendar_id,OWNER_REF)
            service=await asyncio.to_thread(google_service)
            if action=='select':
                index=int(parts[3])
                if not 0<=index<len(session['ids']):raise ValueError()
                await show_plan(query.edit_message_text,session,service,repo,session['ids'][index])
                context.user_data['ai_delete']=session
                return
            result=await asyncio.to_thread(execute,service,repo,session['plan'],calendar_id,OWNER_REF)
            messages={
                'DELETED':'✅ Appuntamento cancellato.',
                'STALE':'Appuntamento cambiato nel frattempo. Serve una nuova selezione e conferma.',
                'BLOCKED':'Cancellazione bloccata: appartenenza non verificabile, richiesta già registrata o operazione precedente irrisolta.',
                'UNKNOWN':'Esito della cancellazione incerto. Verificare Google e PostgreSQL; non ripetere automaticamente.',
            }
            await query.edit_message_text(messages[result])
        except Exception:
            context.user_data.pop('ai_delete',None)
            await query.edit_message_text('Cancellazione interrotta o non confermata. Verificare Google e PostgreSQL; nessun tentativo automatico.')
