"""Explicit isolated DB check: only temporary tables, always rolled back; no Google client."""
import sys
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'telegram-bot'))
from database import get_connection
from reconciliation_service import reconcile_one

class TemporaryCursor:
    def __init__(self, cursor): self.cursor = cursor
    def __enter__(self): self.cursor.__enter__(); return self
    def __exit__(self, *args): return self.cursor.__exit__(*args)
    def execute(self, sql, params):
        return self.cursor.execute(sql.replace('public.booking_operations', 'pg_temp.booking_operations'),params)
    def fetchone(self): return self.cursor.fetchone()
class TemporaryConnection:
    def __init__(self, connection): self.connection = connection
    def cursor(self): return TemporaryCursor(self.connection.cursor())
class ReadOnlyGoogle:
    def events(self): return self
    def get(self, **kwargs): self.event_id=kwargs['eventId']; return self
    def execute(self, num_retries):
        assert num_retries==0
        return {'id':self.event_id,'status':'confirmed','start':{'dateTime':'2026-11-10T10:00:00Z'},'end':{'dateTime':'2026-11-10T11:00:00Z'}}
    def __getattr__(self, name): raise AssertionError('Forbidden Google method')

def main():
    config={}
    for line in (Path(__file__).resolve().parents[1]/'.env').read_text().splitlines():
        key, sep, value=line.partition('=')
        if sep: config[key.strip()]=value.strip()
    connection=get_connection(config['POSTGRES_PASSWORD'])
    try:
        with connection.cursor() as cursor:
            cursor.execute('CREATE TEMP TABLE booking_operations (LIKE public.booking_operations INCLUDING ALL) ON COMMIT DROP')
            for state in ('PENDING','UNKNOWN'):
                event_id=('a' if state=='PENDING' else 'b')*32
                cursor.execute("INSERT INTO pg_temp.booking_operations (request_key,status,google_event_id,calendar_id,owner_ref,execution_version,google_write_started_at) VALUES (%s,%s,%s,'test-calendar','owner',2,NOW()) RETURNING id",(state,state,event_id))
                op_id=cursor.fetchone()[0]
                wrapped=TemporaryConnection(connection)
                assert reconcile_one(wrapped,op_id,ReadOnlyGoogle,'test-calendar','owner',True)=='VERIFIED'
                cursor.execute('SELECT status,last_reconciled_at FROM pg_temp.booking_operations WHERE id=%s',(op_id,))
                assert cursor.fetchone()==(state,None)
                assert reconcile_one(wrapped,op_id,ReadOnlyGoogle,'test-calendar','owner')=='VERIFIED'
                assert reconcile_one(wrapped,op_id,ReadOnlyGoogle,'test-calendar','owner')=='NOT_ELIGIBLE'
                cursor.execute('SELECT status,last_reconciliation_outcome FROM pg_temp.booking_operations WHERE id=%s',(op_id,))
                assert cursor.fetchone()==('CREATED','VERIFIED')
            cursor.execute("INSERT INTO pg_temp.booking_operations (request_key,status) VALUES ('legacy','PENDING') RETURNING id")
            op_id=cursor.fetchone()[0]
            def forbidden(): raise AssertionError('Legacy must never call Google')
            assert reconcile_one(wrapped,op_id,forbidden,'test-calendar','owner')=='LEGACY_REVIEW_REQUIRED'
        print('PostgreSQL: preview, both transitions, idempotency, legacy passed; temporary data rolled back')
    finally:
        connection.rollback()
        connection.close()

if __name__=='__main__': main()
