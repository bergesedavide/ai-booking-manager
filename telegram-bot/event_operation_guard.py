"""Short transaction shared by AI move/delete registrations. No Google I/O."""


def lock_owned_event(cursor, event_id, calendar_id, owner_ref):
    if not calendar_id or calendar_id.strip().lower() == 'primary' or not owner_ref:
        raise ValueError('Invalid operation context')
    # A waiter must see the previous registrant's committed PENDING row in the
    # subsequent SELECT, rather than an earlier repeatable-read snapshot.
    cursor.execute('SHOW transaction_isolation')
    if cursor.fetchone()[0] != 'read committed':
        raise RuntimeError('READ COMMITTED required for event registration')
    cursor.execute(
        "SELECT id FROM public.booking_operations WHERE google_event_id=%s "
        "AND calendar_id=%s AND owner_ref=%s AND execution_version=2 "
        "AND status='CREATED' AND google_write_started_at IS NOT NULL FOR UPDATE",
        (event_id, calendar_id, owner_ref),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def blocked(cursor, event_id, calendar_id, owner_ref):
    cursor.execute(
        "SELECT EXISTS (SELECT 1 FROM public.booking_reschedules "
        "WHERE google_event_id=%s AND calendar_id=%s AND owner_ref=%s "
        "AND status IN ('PENDING','UNKNOWN')) OR EXISTS "
        "(SELECT 1 FROM public.booking_deletions "
        "WHERE google_event_id=%s AND calendar_id=%s AND owner_ref=%s "
        "AND status IN ('PENDING','UNKNOWN','DELETED'))",
        (event_id, calendar_id, owner_ref, event_id, calendar_id, owner_ref),
    )
    return cursor.fetchone()[0]
