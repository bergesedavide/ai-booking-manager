"""Single-operation reconciliation. The only Google method used is events.get."""
from dataclasses import dataclass
from datetime import datetime
from googleapiclient.errors import HttpError


@dataclass(frozen=True)
class Operation:
    id: int
    status: str
    google_event_id: str | None
    calendar_id: str | None
    owner_ref: str | None
    execution_version: int
    google_write_started_at: datetime | None
    updated_at: datetime


def inspect_operation(operation, service_factory, calendar_id, owner_ref):
    if not calendar_id or calendar_id.strip().lower() == "primary" or not owner_ref:
        raise ValueError("Contesto non valido")
    if operation.status not in ("PENDING", "UNKNOWN"):
        return "NOT_ELIGIBLE"
    if operation.execution_version != 2:
        return "LEGACY_REVIEW_REQUIRED"
    if operation.calendar_id != calendar_id or operation.owner_ref != owner_ref:
        return "CONTEXT_MISMATCH"
    event_id = operation.google_event_id
    if not event_id or not 5 <= len(event_id) <= 1024 or any(
        char not in "0123456789abcdefghijklmnopqrstuv" for char in event_id
    ):
        return "INVALID_EVENT_ID"
    if operation.google_write_started_at is None:
        return "WRITE_NOT_STARTED"
    try:
        event = service_factory().events().get(
            calendarId=calendar_id, eventId=event_id,
        ).execute(num_retries=0)
        if not isinstance(event, dict) or event.get("id") != event_id:
            return "INVALID_RESPONSE"
        if event.get("status") == "cancelled":
            return "CANCELLED"
        if event.get("status") != "confirmed":
            return "UNVERIFIED_EVENT"
        if "recurrence" in event or "recurringEventId" in event:
            return "UNVERIFIED_EVENT"
        start = datetime.fromisoformat(event["start"]["dateTime"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(event["end"]["dateTime"].replace("Z", "+00:00"))
        if start.utcoffset() is None or end.utcoffset() is None or end <= start:
            return "INVALID_RESPONSE"
        return "VERIFIED"
    except HttpError as error:
        if error.resp.status == 404:
            return "NOT_FOUND"
        return "GOOGLE_ERROR"
    except (KeyError, TypeError, ValueError):
        return "INVALID_RESPONSE"
    except Exception:
        # Never include exception strings: they may contain credentials or event data.
        return "GOOGLE_ERROR"


def reconcile_one(connection, operation_id, service_factory, calendar_id, owner_ref, preview=False):
    """Caller owns transaction and reports success only after its commit succeeds."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, status, google_event_id, calendar_id, owner_ref, execution_version, "
            "google_write_started_at, updated_at FROM public.booking_operations "
            "WHERE id=%s FOR UPDATE NOWAIT", (operation_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return "OPERATION_NOT_FOUND"
        operation = Operation(*row)
        outcome = inspect_operation(operation, service_factory, calendar_id, owner_ref)
        if preview or outcome == "NOT_ELIGIBLE":
            return outcome
        # Failed verification preserves existing state (including legacy PENDING).
        target = "CREATED" if outcome == "VERIFIED" else operation.status
        cursor.execute(
            "UPDATE public.booking_operations SET status=%s, last_reconciled_at=NOW(), "
            "last_reconciliation_outcome=%s, updated_at=NOW() "
            "WHERE id=%s AND status=%s AND status IN ('PENDING','UNKNOWN') "
            "AND updated_at=%s AND google_event_id IS NOT DISTINCT FROM %s "
            "AND calendar_id IS NOT DISTINCT FROM %s AND owner_ref IS NOT DISTINCT FROM %s "
            "AND execution_version=%s AND google_write_started_at IS NOT DISTINCT FROM %s "
            "RETURNING id",
            (target, outcome, operation.id, operation.status, operation.updated_at,
             operation.google_event_id, operation.calendar_id, operation.owner_ref,
             operation.execution_version, operation.google_write_started_at),
        )
        if cursor.fetchone() is None:
            raise RuntimeError("Aggiornamento concorrente")
        return outcome
