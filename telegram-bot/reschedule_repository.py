"""Separate modification journal; never changes booking_operations."""
from database import get_connection


class RescheduleRepository:
    def __init__(self, password, calendar_id, owner_ref):
        self.password, self.calendar_id, self.owner_ref = password, calendar_id, owner_ref

    def owned(self, event_id):
        with get_connection(self.password) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id FROM public.booking_operations WHERE google_event_id=%s "
                    "AND calendar_id=%s AND owner_ref=%s AND execution_version=2 "
                    "AND status='CREATED' AND google_write_started_at IS NOT NULL",
                    (event_id, self.calendar_id, self.owner_ref),
                )
                row = cursor.fetchone()
                return row[0] if row else None

    def register(self, plan):
        # Recheck provenance within INSERT. Commit before any Google write.
        with get_connection(self.password) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO public.booking_reschedules (booking_operation_id, request_key, "
                    "owner_ref, calendar_id, google_event_id, expected_etag, original_start, "
                    "original_end, requested_start, requested_end, status) "
                    "SELECT id,%s,owner_ref,calendar_id,google_event_id,%s,%s,%s,%s,%s,'PENDING' "
                    "FROM public.booking_operations WHERE google_event_id=%s AND calendar_id=%s "
                    "AND owner_ref=%s AND execution_version=2 AND status='CREATED' "
                    "AND google_write_started_at IS NOT NULL "
                    "ON CONFLICT DO NOTHING RETURNING id",
                    (plan.request_key, plan.etag, plan.old_start, plan.old_end,
                     plan.new_start, plan.new_end, plan.event_id, self.calendar_id, self.owner_ref),
                )
                row = cursor.fetchone()
                return row[0] if row else None

    def start(self, operation_id):
        with get_connection(self.password) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE public.booking_reschedules SET google_write_started_at=now(),updated_at=now() "
                    "WHERE id=%s AND owner_ref=%s AND calendar_id=%s AND status='PENDING' "
                    "AND google_write_started_at IS NULL RETURNING id",
                    (operation_id, self.owner_ref, self.calendar_id),
                )
                if cursor.fetchone() is None:
                    raise RuntimeError('Write boundary rejected')

    def finish(self, operation_id, status, outcome):
        with get_connection(self.password) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE public.booking_reschedules SET status=%s,outcome=%s,updated_at=now() "
                    "WHERE id=%s AND owner_ref=%s AND calendar_id=%s AND status='PENDING' RETURNING id",
                    (status, outcome, operation_id, self.owner_ref, self.calendar_id),
                )
                if cursor.fetchone() is None:
                    raise RuntimeError('Journal update rejected')
