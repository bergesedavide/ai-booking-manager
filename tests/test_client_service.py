"""Offline validation and transaction model; never opens real PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock, get_ident
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'telegram-bot'))
from client_service import ClientService, ClientStatus, _name_key


class Store:
    def __init__(self):
        self.trainers = {}
        self.clients = []
        self.operations = []
        self.events = []
        self.next_operation_id = 1
        self.fail_commit_at = 1
        self.locks = {}
        self.mutex = Lock()
        self.next_id = 1
        self.connections = []
        self.fail = None
        self.commit_error = None
        self.isolation = 'read committed'

    def connect(self, password):
        connection = Connection(self)
        self.connections.append(connection)
        return connection


class Connection:
    def __init__(self, store):
        self.store = store
        self.thread = get_ident()
        self.queries = []
        self.pending = []
        self.pending_operations = []
        self.operation_updates = {}
        self.lock = None
        self.closed = False
        self.rolled_back = False
        self.committed = False
        self.row = None

    def check(self):
        assert get_ident() == self.thread
        assert not self.closed

    def __enter__(self):
        self.check()
        return self

    def __exit__(self, kind, error, trace):
        self.check()
        try:
            if kind:
                self.rolled_back = True
            else:
                if self.store.commit_error == 'before' and self.store.connections.index(self)+1 == self.store.fail_commit_at:
                    raise ConnectionError('private commit details')
                with self.store.mutex:
                    self.store.clients.extend(self.pending)
                    self.store.operations.extend(self.pending_operations)
                    self.store.operations[:] = [self.operation_updates.get(r[0], r) for r in self.store.operations]
                    self.store.events.append(('commit', self.store.connections.index(self)+1))
                self.committed = True
                if self.store.commit_error == 'after' and self.store.connections.index(self)+1 == self.store.fail_commit_at:
                    raise ConnectionError('private commit details')
        finally:
            if self.lock:
                self.lock.release()
            self.closed = True

    def cursor(self):
        connection = self
        class Cursor:
            def __enter__(self):
                connection.check()
                return self
            def __exit__(self, *args):
                connection.check()
            def execute(self, sql, params=()):
                connection.execute(sql, params)
            def fetchone(self):
                connection.check()
                return connection.row
        return Cursor()

    def execute(self, sql, params):
        self.check()
        self.queries.append((sql, params))
        if self.store.fail and self.store.fail in sql:
            raise RuntimeError('private SQL details')
        if sql.startswith('SHOW '):
            self.row = (self.store.isolation,)
        elif sql.startswith('SET TRANSACTION'):
            self.row = None
        elif sql.startswith('INSERT INTO public.trainers'):
            assert 'ON CONFLICT (telegram_user_id) DO NOTHING' in sql
            with self.store.mutex:
                if params[0] not in self.store.trainers:
                    trainer = len(self.store.trainers) + 1
                    self.store.trainers[params[0]] = trainer
                    self.store.locks[trainer] = Lock()
        elif sql.startswith('SELECT id FROM public.trainers'):
            assert 'WHERE telegram_user_id=%s FOR UPDATE' in sql
            trainer = self.store.trainers[params[0]]
            self.lock = self.store.locks[trainer]
            self.lock.acquire()
            self.row = (trainer,)
        elif sql.startswith('SELECT id,status,client_id'):
            assert self.lock and self.lock.locked()
            assert 'trainer_id=%s AND request_key=%s' in sql
            self.row = next(((r[0],r[5],r[6]) for r in self.store.operations if r[1:3]==params),None)
        elif sql.startswith('SELECT status,client_id,first_name_key'):
            assert self.lock and self.lock.locked()
            assert 'trainer_id=%s AND request_key=%s AND id=%s FOR UPDATE' in sql
            self.row = next(((r[5],r[6],r[3],r[4]) for r in self.store.operations
                             if (r[1],r[2],r[0])==params),None)
        elif sql.startswith('SELECT EXISTS (SELECT 1 FROM public.client_operations'):
            assert self.lock and self.lock.locked()
            self.row = (any((r[1],r[3],r[4])==params and r[5]=='PENDING'
                             for r in self.store.operations),)
        elif sql.startswith('INSERT INTO public.client_operations'):
            assert self.lock and self.lock.locked()
            assert not any(r[1:3]==params[:2] for r in self.store.operations)
            assert not any((r[1],r[3],r[4])==(params[0],params[2],params[3]) and r[5]=='PENDING'
                           for r in self.store.operations)
            with self.store.mutex:
                operation_id=self.store.next_operation_id;self.store.next_operation_id+=1
            self.pending_operations.append((operation_id,*params,'PENDING',None))
            self.row=(operation_id,)
        elif sql.startswith('UPDATE public.client_operations'):
            assert self.lock and self.lock.locked()
            client_id,trainer_id,key,operation_id=params
            row=next((r for r in self.store.operations if r[0]==operation_id and r[1:3]==(trainer_id,key)
                      and r[5]=='PENDING' and r[6] is None),None)
            self.row=(operation_id,) if row else None
            if row:self.operation_updates[operation_id]=(*row[:5],'CREATED',client_id)
        elif sql.startswith('SELECT o.status'):
            assert 't.telegram_user_id=%s AND o.request_key=%s' in sql
            trainer=self.store.trainers.get(params[0])
            self.row=next(((r[5],r[6]) for r in self.store.operations if r[1:3]==(trainer,params[1])),None)
        elif sql.startswith('SELECT id,first_name'):
            assert self.lock and self.lock.locked()
            assert 'WHERE trainer_id=%s AND request_key=%s' in sql
            self.row = next(((r[0], *r[3:]) for r in self.store.clients if r[1:3] == params), None)
        elif sql.startswith('SELECT EXISTS'):
            assert self.lock and self.lock.locked()
            assert 'trainer_id=%s' in sql
            self.row = (any((r[1], r[-2], r[-1]) == params for r in self.store.clients),)
        elif sql.startswith('INSERT INTO public.clients'):
            assert self.lock and self.lock.locked()
            assert '(trainer_id,request_key,' in sql
            with self.store.mutex:
                client = self.store.next_id
                self.store.next_id += 1
            assert not any(r[1:3] == params[:2] for r in self.store.clients)
            assert any(r[1:3]==params[:2] and r[5]=='PENDING' for r in self.store.operations)
            self.store.events.append(('client_insert', self.store.connections.index(self)+1))
            self.pending.append((client, *params))
            self.row = (client,)
        elif sql.startswith('SELECT c.id'):
            assert 't.telegram_user_id=%s AND c.request_key=%s' in sql
            trainer = self.store.trainers.get(params[0])
            self.row = next(((r[0],) for r in self.store.clients if r[1:3] == (trainer, params[1])), None)
        else:
            raise AssertionError('Unexpected SQL: ' + sql)


@pytest.fixture
def store():
    return Store()


@pytest.fixture
def service(store):
    return ClientService('fake-password', connection_factory=store.connect)


def add(service, **kwargs):
    args = dict(authorized_telegram_user_id=123, request_key='message-1', first_name='Mario', last_name='Rossi')
    args.update(kwargs)
    return service.add_client(**args)


@pytest.mark.parametrize('field,limit', [('first_name',100),('last_name',100),('phone',30),('email',254),('notes',2000)])
def test_limits(service, store, field, limit):
    assert add(service, **{field:'x'*limit}).status is ClientStatus.CREATED
    before = len(store.connections)
    with pytest.raises(ValueError):add(service, **{field:'x'*(limit+1)})
    assert len(store.connections) == before


@pytest.mark.parametrize('field', ['first_name','last_name'])
@pytest.mark.parametrize('value', [None,'','  ','\t\n',42,True,'a\x00b'])
def test_required_names(service, store, field, value):
    with pytest.raises(ValueError):add(service, **{field:value})
    assert not store.connections


@pytest.mark.parametrize('field', ['phone','email','notes'])
@pytest.mark.parametrize('value', [42,True,'a\x00b'])
def test_optional_types(service, store, field, value):
    with pytest.raises(ValueError):add(service, **{field:value})
    assert not store.connections


@pytest.mark.parametrize('identity', [None,True,False,0,-1,'123',2**63])
def test_identity_validation(service, store, identity):
    with pytest.raises(ValueError):add(service, authorized_telegram_user_id=identity)
    assert not store.connections


@pytest.mark.parametrize('key', [None,'',' \t',1,'bad\x00key'])
def test_request_key_required(service, store, key):
    with pytest.raises(ValueError):add(service, request_key=key)
    assert not store.connections


@pytest.mark.parametrize('flag', ['yes',1,None])
def test_explicit_homonym_boolean(service, store, flag):
    with pytest.raises(ValueError):add(service, allow_homonym=flag)
    assert not store.connections


def test_names_preserved_and_normalized(service, store):
    result = add(service, first_name='  E\u0301LÈNA  Maria ', last_name="D’Angelo-Silva", phone=' ', email=None, notes=' test ')
    assert result.status is ClientStatus.CREATED
    row = store.clients[0]
    assert row[3:8] == ('ÉLÈNA  Maria', 'D’Angelo-Silva', None, None, 'test')
    assert row[-2:] == ('élèna maria','d’angelo-silva')
    assert _name_key('Elena') != _name_key('Elèna')
    assert _name_key("D'Angelo") != _name_key('Dangelo')


def test_same_request_is_idempotent(service, store):
    first = add(service)
    second = add(service)
    assert first.status is ClientStatus.CREATED
    assert second.status is ClientStatus.ALREADY_EXISTS
    assert first.client_id == second.client_id and len(store.clients) == 1
    assert len(store.trainers) == 1


def test_same_request_different_data_never_overwrites(service, store):
    first = add(service)
    second = add(service, first_name='Luigi', allow_homonym=True)
    assert second.status is ClientStatus.REQUEST_CONFLICT and second.client_id == first.client_id
    assert store.clients[0][3] == 'Mario' and len(store.clients) == 1


def test_opaque_request_key_not_normalized(service, store):
    assert add(service, request_key=' ID ').status is ClientStatus.CREATED
    assert store.clients[0][2] == ' ID '


def test_homonym_requires_authorization(service, store):
    add(service)
    blocked = add(service, request_key='message-2', first_name=' MARIO ')
    assert blocked.status is ClientStatus.HOMONYM_REQUIRES_CONFIRMATION
    assert blocked.client_id is None and len(store.clients) == 1
    confirmed = add(service, request_key='message-2', first_name=' MARIO ', allow_homonym=True)
    assert confirmed.status is ClientStatus.CREATED and len(store.clients) == 2
    assert add(service, request_key='message-2', first_name=' MARIO ', allow_homonym=True).client_id == confirmed.client_id


def test_trainer_isolation(service, store):
    a = add(service)
    b = add(service, authorized_telegram_user_id=456)
    assert a.status is b.status is ClientStatus.CREATED and a.client_id != b.client_id
    assert len(store.trainers) == 2
    assert service.lookup_request(authorized_telegram_user_id=456, request_key='message-1').client_id == b.client_id
    assert service.lookup_request(authorized_telegram_user_id=999, request_key='message-1').status is ClientStatus.NOT_FOUND
    assert 999 not in store.trainers  # Receipt lookup never creates a trainer.


@pytest.mark.parametrize('same_request', [True,False])
def test_concurrent_insertions_serialized_by_trainer(service, store, same_request):
    barrier = Barrier(2)
    def worker(index):
        barrier.wait(timeout=2)
        return add(service, request_key='same' if same_request else str(index))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker,i) for i in range(2)]
        results = [f.result(timeout=3) for f in futures]
    assert len(store.clients) == 1 and len(store.trainers) == 1
    statuses = [r.status for r in results]
    assert statuses.count(ClientStatus.CREATED) == 1
    expected_other = ClientStatus.ALREADY_EXISTS if same_request else ClientStatus.HOMONYM_REQUIRES_CONFIRMATION
    assert all(status in (ClientStatus.CREATED, expected_other, ClientStatus.UNRESOLVED) for status in statuses)
    assert all(c.closed and c.committed for c in store.connections)


@pytest.mark.parametrize('phase', ['INSERT INTO public.trainers','FOR UPDATE','SELECT id,first_name','SELECT EXISTS','INSERT INTO public.clients'])
def test_query_failure_rolls_back_without_retry(service, store, phase):
    store.fail = phase
    result = add(service)
    assert result == type(result)(ClientStatus.UNKNOWN if phase=='INSERT INTO public.clients' else ClientStatus.UNAVAILABLE)
    assert not store.clients
    assert len(store.connections)==(2 if phase=='INSERT INTO public.clients' else 1)
    assert store.connections[-1].closed and store.connections[-1].rolled_back


@pytest.mark.parametrize('mode', ['before','after'])
@pytest.mark.parametrize('boundary', [1,2])
def test_commit_uncertain_and_explicit_read_only_lookup(service, store, mode, boundary):
    store.commit_error = mode
    store.fail_commit_at = boundary
    result = add(service)
    assert result.status is ClientStatus.UNKNOWN and result.client_id is None
    assert len(store.connections) == boundary and all(c.closed for c in store.connections)
    assert not store.clients or (boundary==2 and mode=='after')
    store.commit_error = None
    lookup = service.lookup_request(authorized_telegram_user_id=123, request_key='message-1')
    expected = (ClientStatus.NOT_FOUND if boundary==1 and mode=='before' else
                ClientStatus.ALREADY_EXISTS if boundary==2 and mode=='after' else ClientStatus.UNRESOLVED)
    assert lookup.status is expected
    queries = store.connections[-1].queries
    assert queries[0][0] == 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'
    assert all(not sql.startswith(('INSERT','UPDATE','DELETE')) and 'FOR UPDATE' not in sql for sql,_ in queries)
    if store.operations:
        assert store.operations[0][5] == ('CREATED' if store.clients else 'PENDING')


def test_fail_closed_isolation_and_no_sensitive_logs(service, store, capsys):
    store.isolation = 'repeatable read'
    assert add(service, first_name='private-name').status is ClientStatus.UNAVAILABLE
    assert not store.trainers and not store.clients
    assert 'private' not in capsys.readouterr().out


def test_result_returned_after_commit_and_close(service, store):
    result = add(service)
    assert result.status is ClientStatus.CREATED
    connection = store.connections[0]
    assert connection.closed and connection.committed
    sqls = [sql for sql,_ in connection.queries]
    assert sqls[0] == 'SHOW transaction_isolation'
    assert sqls.index(next(s for s in sqls if 'FOR UPDATE' in s)) < sqls.index(next(s for s in sqls if s.startswith('SELECT id,first_name')))
    assert not any('ON CONSTRAINT' in sql for sql in sqls)


def test_two_authorized_homonyms_can_be_inserted_concurrently(service, store):
    add(service)
    barrier = Barrier(2)
    def worker(i):
        barrier.wait(timeout=2)
        return add(service, request_key=f'confirmed-{i}', allow_homonym=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker,i) for i in range(2)]
        results = [f.result(timeout=3) for f in futures]
    assert all(r.status in (ClientStatus.CREATED, ClientStatus.UNRESOLVED) for r in results)
    assert len({r[0] for r in store.clients}) == 1 + sum(r.status is ClientStatus.CREATED for r in results)


def test_homonym_appeared_between_preview_and_insert_is_blocked(service, store):
    # No preliminary lookup authorizes insertion: every call checks under lock.
    assert service.lookup_request(authorized_telegram_user_id=123, request_key='later').status is ClientStatus.NOT_FOUND
    add(service)
    assert add(service, request_key='later').status is ClientStatus.HOMONYM_REQUIRES_CONFIRMATION
    assert len(store.clients) == 1


def test_lookup_error_is_sanitized_and_does_not_write(service, store, capsys):
    store.fail = 'SELECT c.id'
    assert service.lookup_request(authorized_telegram_user_id=123, request_key='private-key').status is ClientStatus.UNAVAILABLE
    assert not store.clients and not store.trainers
    assert len(store.connections) == 1 and store.connections[0].rolled_back and store.connections[0].closed
    assert capsys.readouterr().out == ''


def test_connection_failure_is_unavailable_without_retry():
    calls = []
    def connect(password):
        calls.append(1)
        raise ConnectionError('private credentials')
    service = ClientService('fake-password', connection_factory=connect)
    assert add(service).status is ClientStatus.UNAVAILABLE
    assert calls == [1]


def test_checkpoint_commit_precedes_client_insert_and_finalization_is_atomic(service, store):
    result=add(service)
    assert result.status is ClientStatus.CREATED
    assert store.events==[('commit',1),('client_insert',2),('commit',2)]
    operation=store.operations[0]
    assert operation[5:] == ('CREATED',result.client_id)
    assert store.clients[0][0]==operation[6]
    assert all(c.closed for c in store.connections)
    queries=[sql for sql,_ in store.connections[1].queries]
    assert queries.index(next(q for q in queries if q.startswith('SELECT id FROM public.trainers'))) < queries.index(next(q for q in queries if q.startswith('SELECT status,client_id')))


def test_existing_pending_never_resumed_even_with_confirmation_after_restart(service, store):
    from client_repository import ClientData
    data=ClientData('Mario','Rossi',None,None,None,'mario','rossi')
    checkpoint=service._repository._prepare(123,'message-1',data,False)
    assert checkpoint.operation_id and store.operations[0][5]=='PENDING' and not store.clients
    restarted=ClientService('fake-password',connection_factory=store.connect)
    assert add(restarted).status is ClientStatus.UNRESOLVED
    assert add(restarted,allow_homonym=True).status is ClientStatus.UNRESOLVED
    assert add(restarted,request_key='new-message').status is ClientStatus.UNRESOLVED
    assert add(restarted,request_key='confirmed-new',allow_homonym=True).status is ClientStatus.UNRESOLVED
    assert not store.clients and len(store.operations)==1
    assert not any(sql.startswith('INSERT INTO public.clients') for c in store.connections for sql,_ in c.queries)


def test_pending_does_not_block_other_names_or_other_trainers(service, store):
    from client_repository import ClientData
    data=ClientData('Mario','Rossi',None,None,None,'mario','rossi')
    service._repository._prepare(123,'message-1',data,False)
    assert add(service,request_key='other-name',first_name='Luigi').status is ClientStatus.CREATED
    assert add(service,authorized_telegram_user_id=456).status is ClientStatus.CREATED
    assert len(store.clients)==2
    assert sorted(r[5] for r in store.operations)==['CREATED','CREATED','PENDING']


def test_homonym_question_creates_no_checkpoint(service, store):
    add(service)
    assert add(service,request_key='homonym').status is ClientStatus.HOMONYM_REQUIRES_CONFIRMATION
    assert len(store.operations)==1 and store.operations[0][5]=='CREATED'
    assert add(service,request_key='homonym',allow_homonym=True).status is ClientStatus.CREATED
    assert len(store.operations)==2


def test_legacy_clients_deduplicated_without_fabricated_receipts(service, store):
    add(service)
    store.operations.clear()  # Model a client predating the journal.
    assert add(service).status is ClientStatus.ALREADY_EXISTS
    assert service.lookup_request(authorized_telegram_user_id=123,request_key='message-1').status is ClientStatus.ALREADY_EXISTS
    assert not store.operations and len(store.clients)==1


@pytest.mark.parametrize('failure',['INSERT INTO public.clients','UPDATE public.client_operations'])
def test_final_transaction_rollback_preserves_pending_and_never_partially_commits(service, store, failure):
    store.fail=failure
    assert add(service).status is ClientStatus.UNKNOWN
    assert not store.clients and store.operations[0][5:] == ('PENDING',None)
    assert store.connections[1].rolled_back and all(c.closed for c in store.connections)
    store.fail=None
    assert add(service).status is ClientStatus.UNRESOLVED
    assert len(store.operations)==1 and not store.clients


def test_created_receipt_never_downgraded_after_lost_final_commit(service, store):
    store.commit_error='after';store.fail_commit_at=2
    assert add(service).status is ClientStatus.UNKNOWN
    assert len(store.clients)==1 and store.operations[0][5]=='CREATED'
    store.commit_error=None
    restarted=ClientService('fake-password',connection_factory=store.connect)
    assert add(restarted).status is ClientStatus.ALREADY_EXISTS
    assert len(store.clients)==1 and store.operations[0][5]=='CREATED'


def test_concurrent_pending_name_attempts_are_both_blocked(service, store):
    from client_repository import ClientData
    data=ClientData('Mario','Rossi',None,None,None,'mario','rossi')
    service._repository._prepare(123,'original',data,False)
    barrier=Barrier(2)
    def worker(index):
        barrier.wait(timeout=2)
        return add(service,request_key=f'new-{index}',allow_homonym=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(worker,i) for i in range(2)]
        results=[f.result(timeout=3) for f in futures]
    assert all(r.status is ClientStatus.UNRESOLVED for r in results)
    assert not store.clients and len(store.operations)==1


def test_checkpoint_changed_aborts_finalization(service, store):
    from client_repository import ClientData, ClientStorageError
    data=ClientData('Mario','Rossi',None,None,None,'mario','rossi')
    checkpoint=service._repository._prepare(123,'key',data,False)
    # A changed checkpoint must never authorize a new client insert.
    old=store.operations[0]
    store.operations[0]=(*old[:3],'different',old[4],*old[5:])
    with pytest.raises(ClientStorageError) as caught:
        service._repository._complete(123,'key',data,checkpoint)
    assert caught.value.commit_uncertain and not store.clients
    assert store.connections[-1].rolled_back and store.connections[-1].closed


def test_foreign_trainer_receipt_is_invisible_and_cannot_block(service, store):
    from client_repository import ClientData
    data=ClientData('Mario','Rossi',None,None,None,'mario','rossi')
    service._repository._prepare(123,'same-key',data,False)
    assert service.lookup_request(authorized_telegram_user_id=456,request_key='same-key').status is ClientStatus.NOT_FOUND
    result=add(service,authorized_telegram_user_id=456,request_key='same-key')
    assert result.status is ClientStatus.CREATED
    assert store.operations[0][5]=='PENDING' and store.operations[1][5]=='CREATED'


def test_read_only_lookup_does_not_complete_a_pending(service, store):
    from client_repository import ClientData
    data=ClientData('Mario','Rossi',None,None,None,'mario','rossi')
    service._repository._prepare(123,'key',data,False)
    assert service.lookup_request(authorized_telegram_user_id=123,request_key='key').status is ClientStatus.UNRESOLVED
    sqls=[sql for sql,_ in store.connections[-1].queries]
    assert sqls[0]=='SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'
    assert not any(sql.startswith(('INSERT','UPDATE','DELETE')) or 'FOR UPDATE' in sql for sql in sqls)
    assert not store.clients


def test_0083_schema_matches_two_state_contract():
    import re
    sql=(Path(__file__).resolve().parents[1]/'migrations/0083_client_operations.sql').read_text()
    body=re.sub(r'--[^\n]*','',sql).strip()
    assert body.startswith('BEGIN;') and body.endswith('COMMIT;')
    assert "CHECK (status IN ('PENDING', 'CREATED'))" in body and 'UNKNOWN' not in body
    assert 'UNIQUE (trainer_id, request_key)' in body
    assert "WHERE status = 'PENDING'" in body
    assert 'ON public.client_operations (trainer_id, first_name_key, last_name_key)' in body
    assert "status = 'CREATED' AND client_id IS NOT NULL" in body
    assert "status = 'PENDING' AND client_id IS NULL" in body
    assert 'REFERENCES public.trainers(id)' in body and 'REFERENCES public.clients(id)' in body
    assert not re.search(r'\b(ALTER|DROP|INSERT|DELETE|UPDATE)\b',body)
