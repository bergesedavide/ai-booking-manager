"""AI deletion journal. Creation history remains unchanged."""
from database import get_connection
from event_operation_guard import lock_owned_event, blocked


class DeletionRepository:
    def __init__(self, password, calendar_id, owner_ref):
        self.password, self.calendar_id, self.owner_ref = password, calendar_id, owner_ref

    def owned(self, event_id):
        # Used by search/planning. Registration repeats these checks under lock.
        with get_connection(self.password) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id FROM public.booking_operations WHERE google_event_id=%s "
                    "AND calendar_id=%s AND owner_ref=%s AND execution_version=2 "
                    "AND status='CREATED' AND google_write_started_at IS NOT NULL",
                    (event_id, self.calendar_id, self.owner_ref),
                )
                row = cursor.fetchone()
                if not row or blocked(cursor, event_id, self.calendar_id, self.owner_ref):
                    return None
                return row[0]

    def register(self, plan):
        if (plan.calendar_id, plan.owner_ref) != (self.calendar_id, self.owner_ref):
            return None
        with get_connection(self.password) as connection:
            with connection.cursor() as cursor:
                booking_id = lock_owned_event(cursor, plan.event_id, self.calendar_id, self.owner_ref)
                if not booking_id or blocked(cursor, plan.event_id, self.calendar_id, self.owner_ref):
                    return None
                cursor.execute(
                    "INSERT INTO public.booking_deletions (booking_operation_id,request_key,owner_ref,"
                    "calendar_id,google_event_id,expected_etag,original_start,original_end,status) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'PENDING') "
                    "ON CONFLICT DO NOTHING RETURNING id",
                    (booking_id, plan.request_key, self.owner_ref, self.calendar_id,
                     plan.event_id, plan.etag, plan.old_start, plan.old_end),
                )
                row = cursor.fetchone()
                return row[0] if row else None

    def start(self, operation_id):
        with get_connection(self.password) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE public.booking_deletions SET google_write_started_at=now(),updated_at=now() "
                    "WHERE id=%s AND owner_ref=%s AND calendar_id=%s AND status='PENDING' "
                    "AND google_write_started_at IS NULL RETURNING id",
                    (operation_id, self.owner_ref, self.calendar_id),
                )
                if cursor.fetchone() is None:
                    raise RuntimeError('Deletion write boundary rejected')

    def finish(self, operation_id, status, outcome):
        with get_connection(self.password) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE public.booking_deletions SET status=%s,outcome=%s,updated_at=now() "
                    "WHERE id=%s AND owner_ref=%s AND calendar_id=%s AND status='PENDING' RETURNING id",
                    (status, outcome, operation_id, self.owner_ref, self.calendar_id),
                )
                if cursor.fetchone() is None:
                    raise RuntimeError('Deletion journal update rejected')
