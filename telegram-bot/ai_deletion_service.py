"""Deterministic AI deletion planning; one conditional DELETE, no retries."""
from dataclasses import dataclass, replace
from datetime import datetime
from googleapiclient.errors import HttpError
from ai_reschedule_service import candidates as move_candidates, interval, validate_context, ROME
from booking_service import get_booking
from google_calendar import delete_booking


def search_intent(intent):
    # Cancellation schema uses data/ora; M06.7 search uses original fields.
    return replace(intent, data_originale=intent.data, ora_originale=intent.ora)


def candidates(service, repo, intent, calendar_id, owner_ref):
    return move_candidates(service, repo, search_intent(intent), calendar_id, owner_ref)


@dataclass(frozen=True)
class DeletePlan:
    request_key: str
    event_id: str
    calendar_id: str
    owner_ref: str
    etag: str
    title: str
    old_start: datetime
    old_end: datetime


def prepare(service, repo, intent, event_id, request_key, calendar_id, owner_ref):
    validate_context(calendar_id, owner_ref)
    if not repo.owned(event_id):
        raise ValueError('Event not eligible')
    event = get_booking(service, calendar_id, event_id)
    if event.get('id') != event_id:
        raise ValueError('Unexpected event ID')
    start, end, _ = interval(event)
    local = start.astimezone(ROME)
    if local.date().isoformat() != intent.data or (intent.ora and local.strftime('%H:%M') != intent.ora):
        raise ValueError('Event changed')
    if ' '.join(event.get('summary','').casefold().split()) != ' '.join(('Allenamento - '+intent.cliente).casefold().split()):
        raise ValueError('Event changed')
    return DeletePlan(request_key,event_id,calendar_id,owner_ref,event['etag'],event.get('summary',''),start,end)


def execute(service, repo, plan, calendar_id, owner_ref):
    validate_context(calendar_id, owner_ref)
    if (plan.calendar_id,plan.owner_ref) != (calendar_id,owner_ref) or not repo.owned(plan.event_id):
        return 'BLOCKED'
    # Absence/errors here must never be treated as successful deletion.
    event = get_booking(service, calendar_id, plan.event_id)
    if event.get('id') != plan.event_id or not plan.etag or event.get('etag') != plan.etag:
        return 'STALE'
    start,end,_ = interval(event)
    if (start,end,event.get('summary','')) != (plan.old_start,plan.old_end,plan.title):
        return 'STALE'
    operation_id = repo.register(plan)  # Row lock + both journals checked atomically.
    if operation_id is None:
        return 'BLOCKED'
    repo.start(operation_id)  # Commit marker before DELETE; failure stops here.
    try:
        delete_booking(service,calendar_id,plan.event_id,expected_etag=plan.etag)
    except HttpError as error:
        status,outcome = ('FAILED','STALE') if error.resp.status == 412 else ('UNKNOWN','UNKNOWN')
    except Exception:
        # Includes decoding errors and ValueError: do not guess the write phase.
        status,outcome = 'UNKNOWN','UNKNOWN'
    else:
        # Only the successful DELETE response establishes success. Never infer it
        # from a later GET 404/410. No post-delete GET or automatic retry.
        status,outcome = 'DELETED','DELETED'
    repo.finish(operation_id,status,outcome)  # Success returned only after commit.
    return outcome
