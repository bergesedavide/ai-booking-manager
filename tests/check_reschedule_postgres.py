"""Explicit isolated repository/schema check. Temporary tables; always rollback."""
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_ai_reschedule import PLAN
from check_reconciliation_postgres import TemporaryConnection, TemporaryCursor
from database import get_connection
from reschedule_repository import RescheduleRepository

class MoveCursor(TemporaryCursor):
    def execute(self, sql, params=None):
        return self.cursor.execute(sql.replace('public.booking_operations','pg_temp.booking_operations').replace('public.booking_reschedules','pg_temp.booking_reschedules'),params)
class MoveConnection(TemporaryConnection):
    def cursor(self):return MoveCursor(self.connection.cursor())


def main():
    root=Path(__file__).resolve().parents[1]
    config={}
    for line in (root/'.env').read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#'):
            k,s,v=line.partition('=')
            if s:config[k.strip()]=v.strip()
    connection=get_connection(config['POSTGRES_PASSWORD'])
    try:
        with connection.cursor() as cursor:
            cursor.execute('CREATE TEMP TABLE booking_operations (LIKE public.booking_operations INCLUDING ALL) ON COMMIT DROP')
            migration=(root/'migrations/0067_reschedules.sql').read_text()
            migration=migration.replace('BEGIN;','').replace('COMMIT;','').replace('CREATE TABLE public.booking_reschedules','CREATE TEMP TABLE booking_reschedules').replace('REFERENCES public.booking_operations','REFERENCES pg_temp.booking_operations').replace('ON public.booking_reschedules','ON pg_temp.booking_reschedules')
            cursor.execute(migration)
            cursor.execute("INSERT INTO pg_temp.booking_operations (request_key,google_event_id,status,execution_version,calendar_id,owner_ref,google_write_started_at) VALUES ('fixture',%s,'CREATED',2,'test-calendar','owner',NOW())",(PLAN.event_id,))
        @contextmanager
        def isolated(_):
            with connection.transaction():
                yield MoveConnection(connection)
        repo=RescheduleRepository('unused','test-calendar','owner')
        with patch('reschedule_repository.get_connection',isolated):
            assert repo.owned('external') is None
            assert repo.owned(PLAN.event_id) is not None
            op=repo.register(PLAN);assert op is not None
            assert repo.register(PLAN) is None
            assert repo.register(replace(PLAN,request_key='other')) is None
            repo.start(op)
            try:repo.start(op)
            except RuntimeError:pass
            else:raise AssertionError('repeated write boundary accepted')
            repo.finish(op,'UPDATED','UPDATED')
            assert repo.register(PLAN) is None
            next_op=repo.register(replace(PLAN,request_key='next'));assert next_op
            repo.start(next_op);repo.finish(next_op,'UNKNOWN','UNKNOWN')
            assert repo.register(replace(PLAN,request_key='third')) is None
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO pg_temp.booking_operations(request_key,google_event_id,status) VALUES ('legacy','legacy','CREATED')")
            assert repo.owned('legacy') is None
        print('Temporary PostgreSQL checks passed: migration, ownership, duplicate requests, uncertain-event lock, conditional transitions. All fixtures rolled back.')
    finally:
        connection.rollback();connection.close()

if __name__=='__main__':main()
