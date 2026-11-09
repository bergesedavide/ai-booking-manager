"""Deterministic AI move planning and execution; no automatic write retries."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from googleapiclient.errors import HttpError
from booking_service import (
    authenticate, build, get_booking,
)
from google_calendar import list_events_in_interval, check_reschedule_availability_safe, update_booking, AvailabilityStatus
from booking_datetime import ROME, parse_ai_booking_start
from ai.schemas import AppointmentIntent


def google_service():
    return build('calendar', 'v3', credentials=authenticate())


def validate_context(calendar_id, owner_ref):
    if not calendar_id or calendar_id.strip().lower() == 'primary' or not owner_ref:
        raise ValueError('Invalid context')


def local_start(day, time, duration):
    # Reuse strict DST validation without changing creation behavior.
    return parse_ai_booking_start(AppointmentIntent('crea', data=day, ora=time, durata_minuti=duration))


def interval(event):
    if event.get('status') != 'confirmed' or not event.get('etag'):
        raise ValueError('Unsupported event')
    if 'recurrence' in event or 'recurringEventId' in event:
        raise ValueError('Recurring event')
    start = datetime.fromisoformat(event['start']['dateTime']).astimezone(timezone.utc)
    end = datetime.fromisoformat(event['end']['dateTime']).astimezone(timezone.utc)
    # Reject naive API timestamps rather than accepting the host timezone.
    if datetime.fromisoformat(event['start']['dateTime']).tzinfo is None or datetime.fromisoformat(event['end']['dateTime']).tzinfo is None:
        raise ValueError('Timezone required')
    minutes = (end-start).total_seconds()/60
    if minutes <= 0 or not minutes.is_integer():
        raise ValueError('Unsupported duration')
    return start, end, int(minutes)


def candidates(service, repo, intent, calendar_id, owner_ref):
    validate_context(calendar_id, owner_ref)
    day = datetime.strptime(intent.data_originale, '%Y-%m-%d').date()
    start = datetime.combine(day, datetime.min.time(), ROME)
    end = datetime.combine(day+timedelta(days=1), datetime.min.time(), ROME)
    expected_title = ' '.join(('Allenamento - '+intent.cliente).casefold().split())
    found = {}
    for event in list_events_in_interval(service, calendar_id, start, end):
        event_id = event.get('id')
        if not event_id or not repo.owned(event_id):
            continue  # Title alone NEVER authorizes a move.
        if ' '.join(event.get('summary','').casefold().split()) != expected_title:
            continue
        try:
            old_start, _, _ = interval(event)
        except (ValueError, KeyError, TypeError):
            continue
        local = old_start.astimezone(ROME)
        if local.date() != day or (intent.ora_originale and local.strftime('%H:%M') != intent.ora_originale):
            continue
        found[event_id] = event
    return list(found.values())


@dataclass(frozen=True)
class MovePlan:
    request_key: str
    event_id: str
    calendar_id: str
    owner_ref: str
    etag: str
    title: str
    old_start: datetime
    old_end: datetime
    new_start: datetime
    new_end: datetime


def prepare(service, repo, intent, event_id, request_key, calendar_id, owner_ref):
    validate_context(calendar_id, owner_ref)
    if not repo.owned(event_id):
        raise ValueError('Unowned event')
    if intent.durata_minuti is not None:
        raise ValueError('Duration changes unsupported')
    event = get_booking(service, calendar_id, event_id)
    if event.get('id') != event_id:
        raise ValueError('Unexpected event ID')
    old_start, old_end, duration = interval(event)
    # Revalidate search criteria after selection, in case the event changed.
    local = old_start.astimezone(ROME)
    if local.date().isoformat() != intent.data_originale or (intent.ora_originale and local.strftime('%H:%M') != intent.ora_originale):
        raise ValueError('Event changed')
    if ' '.join(event.get('summary','').casefold().split()) != ' '.join(('Allenamento - '+intent.cliente).casefold().split()):
        raise ValueError('Event changed')
    new_start = local_start(intent.data, intent.ora, duration).astimezone(timezone.utc)
    plan = MovePlan(request_key, event_id, calendar_id, owner_ref, event['etag'], event.get('summary',''),
                    old_start, old_end, new_start, new_start+timedelta(minutes=duration))
    return plan, availability(service, plan)


def availability(service, plan):
    return check_reschedule_availability_safe(service, plan.calendar_id, plan.event_id,
        plan.new_start, int((plan.new_end-plan.new_start).total_seconds()/60))


def execute(service, repo, plan, calendar_id, owner_ref, allow_conflict=False):
    validate_context(calendar_id, owner_ref)
    if (plan.calendar_id, plan.owner_ref) != (calendar_id, owner_ref) or not repo.owned(plan.event_id):
        return 'REJECTED'
    event = get_booking(service, calendar_id, plan.event_id)
    if event.get('id') != plan.event_id or event.get('etag') != plan.etag:
        return 'STALE'
    start, end, _ = interval(event)
    if (start,end,event.get('summary','')) != (plan.old_start,plan.old_end,plan.title):
        return 'STALE'
    status = availability(service, plan)
    if status not in (AvailabilityStatus.AVAILABLE, AvailabilityStatus.CONFLICT):
        return 'UNAVAILABLE'
    if status == AvailabilityStatus.CONFLICT and not allow_conflict:
        return 'CONFLICT'
    operation_id = repo.register(plan)
    if operation_id is None:
        return 'DUPLICATE_OR_UNRESOLVED'
    repo.start(operation_id)  # Failed/uncertain commit: STOP before PATCH.
    try:
        result = update_booking(service, calendar_id, plan.event_id, plan.new_start, expected_etag=plan.etag)
    except HttpError as error:
        status, outcome = ('FAILED','STALE') if error.resp.status == 412 else ('UNKNOWN','UNKNOWN')
    except Exception:
        status, outcome = 'UNKNOWN', 'UNKNOWN'
    else:
        try:
            a,b,_ = interval(result)
            verified = result.get('id') == plan.event_id and (a,b) == (plan.new_start,plan.new_end) and result.get('summary','') == plan.title
        except (ValueError,KeyError,TypeError):
            verified = False
        status, outcome = ('UPDATED','UPDATED') if verified else ('UNKNOWN','UNKNOWN')
    repo.finish(operation_id, status, outcome)
    return outcome
