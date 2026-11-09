"""Explicit DB integration: disposable schema, real commits/locks, no Google.
Never migrates public tables. Drops the test schema in finally.
"""
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
import sys
from threading import Event, Thread, current_thread
from time import monotonic, sleep
from uuid import uuid4
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_ai_reschedule import PLAN as MOVE
from test_ai_deletion import PLAN as DELETE
from database import get_connection
from reschedule_repository import RescheduleRepository
from deletion_repository import DeletionRepository
import reschedule_repository as moves
import deletion_repository as deletes
from event_operation_guard import lock_owned_event


def main():
    root=Path(__file__).resolve().parents[1]
    config={}
    for line in (root/'.env').read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#'):
            key,sep,value=line.partition('=')
            if sep:config[key.strip()]=value.strip()
    password=config['POSTGRES_PASSWORD']
    schema='m068_test_'+uuid4().hex
    admin=get_connection(password)
    pids={}
    class Cursor:
        def __init__(self,cursor):self.cursor=cursor
        def __enter__(self):self.cursor.__enter__();return self
        def __exit__(self,*args):return self.cursor.__exit__(*args)
        def execute(self,sql,params=None):return self.cursor.execute(sql.replace('public.',schema+'.'),params)
        def fetchone(self):return self.cursor.fetchone()
    class Connection:
        def __init__(self,connection):self.connection=connection
        def __enter__(self):self.connection.__enter__();return self
        def __exit__(self,*args):return self.connection.__exit__(*args)
        def cursor(self):return Cursor(self.connection.cursor())
    def isolated(_):
        connection=get_connection(password)
        pids[current_thread().name]=connection.info.backend_pid
        return Connection(connection)
    def sql(statement,params=None):
        with admin.cursor() as cursor:
            cursor.execute(statement.replace('test_schema.',schema+'.'),params)
            result=cursor.fetchall() if cursor.description else None
        admin.commit()
        return result
    def clear():
        sql('TRUNCATE test_schema.booking_deletions, test_schema.booking_reschedules')
    try:
        sql('CREATE SCHEMA '+schema)
        sql('CREATE TABLE test_schema.booking_operations (LIKE public.booking_operations INCLUDING ALL)')
        for name in ('0067_reschedules.sql','0068_deletions.sql'):
            migration=(root/'migrations'/name).read_text().replace('BEGIN;','').replace('COMMIT;','').replace('public.',schema+'.')
            sql(migration)
        sql("INSERT INTO test_schema.booking_operations (request_key,google_event_id,status,execution_version,calendar_id,owner_ref,google_write_started_at) VALUES ('owned',%s,'CREATED',2,'test-calendar','owner',now()), ('legacy','legacy','CREATED',1,NULL,NULL,NULL)",(DELETE.event_id,))
        move_repo=RescheduleRepository('unused','test-calendar','owner')
        delete_repo=DeletionRepository('unused','test-calendar','owner')
        with ExitStack() as stack:
            stack.enter_context(patch.object(moves,'get_connection',isolated))
            stack.enter_context(patch.object(deletes,'get_connection',isolated))
            assert delete_repo.owned('legacy') is None
            assert delete_repo.owned('external') is None
            assert DeletionRepository('unused','other','owner').owned(DELETE.event_id) is None
            assert DeletionRepository('unused','test-calendar','other').owned(DELETE.event_id) is None
            for status in ('PENDING','UNKNOWN'):
                clear()
                op=move_repo.register(MOVE);assert op
                if status=='UNKNOWN':move_repo.start(op);move_repo.finish(op,'UNKNOWN','UNKNOWN')
                assert delete_repo.owned(DELETE.event_id) is None
                assert delete_repo.register(DELETE) is None
            for status in ('PENDING','UNKNOWN','DELETED'):
                clear()
                op=delete_repo.register(DELETE);assert op
                if status!='PENDING':delete_repo.start(op);delete_repo.finish(op,status,status)
                assert delete_repo.register(replace(DELETE,request_key='another')) is None
                assert move_repo.register(MOVE) is None
            clear()
            op=delete_repo.register(DELETE);delete_repo.start(op)
            try:delete_repo.start(op)
            except RuntimeError:pass
            else:raise AssertionError('Repeated start accepted')
            delete_repo.finish(op,'FAILED','STALE')
            assert delete_repo.register(DELETE) is None  # Request never replayed.
            assert delete_repo.register(replace(DELETE,request_key='new-request'))
            clear()
            # Both orderings: first registrant holds row lock before its INSERT;
            # second must block, then observe committed PENDING and reject.
            for first_kind in ('move','delete'):
                locked=Event();release=Event();results={};errors=[];pids.clear()
                def hook(cursor,*args):
                    result=lock_owned_event(cursor,*args)
                    if current_thread().name=='first':
                        locked.set()
                        if not release.wait(8):raise RuntimeError('Test release timed out')
                    return result
                def register(kind):
                    try:results[kind]=(move_repo.register(MOVE) if kind=='move' else delete_repo.register(DELETE))
                    except Exception as error:errors.append(error)
                second_kind='delete' if first_kind=='move' else 'move'
                with patch.object(moves,'lock_owned_event',hook),patch.object(deletes,'lock_owned_event',hook):
                    first=Thread(target=register,args=(first_kind,),name='first')
                    second=Thread(target=register,args=(second_kind,),name='second')
                    first.start()
                    assert locked.wait(4),'First did not acquire lock'
                    second.start()
                    try:
                        deadline=monotonic()+4
                        blocked=False
                        while monotonic()<deadline:
                            pid=pids.get('second')
                            if pid:
                                blocked=bool(sql('SELECT cardinality(pg_blocking_pids(%s)) > 0',(pid,))[0][0])
                                if blocked:break
                            sleep(.02)
                        assert blocked,'Second did not block on actual PostgreSQL row lock'
                    finally:
                        release.set();first.join(8);second.join(8)
                    assert not first.is_alive() and not second.is_alive()
                    assert not errors,errors
                    assert results[first_kind] is not None and results[second_kind] is None,results
                clear()
        print('PASS: isolated migrations, legacy/external exclusion, cross-journal blockers, duplicates, conditional transitions and both concurrent orderings (real row-lock wait).')
    finally:
        admin.rollback()
        with admin.cursor() as cursor:cursor.execute('DROP SCHEMA IF EXISTS '+schema+' CASCADE')
        admin.commit();admin.close()
        print('Disposable test schema removed; public tables never modified.')

if __name__=='__main__':main()
