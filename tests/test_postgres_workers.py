"""Offline PostgreSQL boundaries and cancellation; no database or network."""
import ast
import asyncio
from contextlib import contextmanager
from pathlib import Path
import sys
import threading
from unittest.mock import Mock
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'telegram-bot'))
import database
import ai_handler as h
from worker_guard import await_worker
from test_ai_creation_confirmation import env, INTENT, CALENDAR


def test_connection_session_timeouts(monkeypatch):
    connect = Mock()
    monkeypatch.setattr(database.psycopg, 'connect', connect)
    database.get_connection('fake-password')
    args = connect.call_args.kwargs
    assert args['connect_timeout'] == 5
    assert args['options'] == '-c statement_timeout=10000 -c lock_timeout=3000'
    connect.assert_called_once()


@pytest.mark.parametrize('error', [database.psycopg.errors.QueryCanceled, database.psycopg.errors.LockNotAvailable])
def test_transaction_error_rolls_back_closes_same_thread(monkeypatch, error):
    events = []
    @contextmanager
    def connection(_):
        events.append(('open', threading.get_ident()))
        try:
            yield object()
        except Exception:
            events.append(('rollback', threading.get_ident()))
            raise
        else:
            events.append(('commit', threading.get_ident()))
        finally:
            events.append(('close', threading.get_ident()))
    def update(*args):
        events.append(('query', threading.get_ident()))
        raise error('simulated timeout')
    monkeypatch.setattr(h, 'get_connection', connection)
    async def scenario():
        with pytest.raises(error):
            await await_worker(h.save_booking, 'unused', update, 'request')
    asyncio.run(scenario())
    assert [e[0] for e in events] == ['open', 'query', 'rollback', 'close']
    assert len({e[1] for e in events}) == 1
    assert events[0][1] != threading.get_ident()


@pytest.mark.parametrize('fails', [False, True])
def test_repeated_cancellation_waits_for_worker(fails):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def worker():
        started.set()
        try:
            assert release.wait(2)
            calls.append('write')
            if fails:
                raise RuntimeError('simulated commit uncertainty')
            return 1
        finally:
            finished.set()
    async def scenario():
        task = asyncio.create_task(await_worker(worker))
        try:
            for _ in range(200):
                if started.is_set(): break
                await asyncio.sleep(.001)
            assert started.is_set()
            task.cancel()
            await asyncio.sleep(.01)
            task.cancel()
            await asyncio.sleep(.01)
            assert not task.done() and not finished.is_set()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert finished.is_set()
    asyncio.run(scenario())
    assert calls == ['write']


def test_slow_read_leaves_event_loop_responsive(monkeypatch):
    started, release = threading.Event(), threading.Event()
    ids = []
    @contextmanager
    def connection(_):
        ids.append(threading.get_ident())
        try: yield object()
        finally: ids.append(threading.get_ident())
    def read(*args):
        ids.append(threading.get_ident())
        started.set()
        assert release.wait(2)
        return ('CREATED', 'fake-event')
    monkeypatch.setattr(h, 'get_connection', connection)
    monkeypatch.setattr(h, 'get_booking_operation', read)
    async def scenario():
        task = asyncio.create_task(h.format_ai_result(INTENT, CALENDAR, 'key', 'unused'))
        try:
            for _ in range(200):
                if started.is_set(): break
                await asyncio.sleep(.001)
            assert started.is_set() and not task.done()
            await asyncio.sleep(.01)
        finally:
            release.set()
            result = await task
        assert 'CREATED' in result
    asyncio.run(scenario())
    assert len(set(ids)) == 1 and ids[0] != threading.get_ident()


def test_no_direct_connections_in_async_creation_functions():
    tree = ast.parse(Path(h.__file__).read_text())
    for function in ast.walk(tree):
        if isinstance(function, ast.AsyncFunctionDef):
            assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                           and n.func.id == 'get_connection' for n in ast.walk(function))


@pytest.mark.parametrize('phase', ['read', 'register', 'marker', 'final'])
def test_db_failure_never_retries_or_claims_success(env, monkeypatch, phase):
    def fail(*args): raise RuntimeError('simulated uncertain commit')
    if phase == 'read': monkeypatch.setattr(h, 'get_booking_operation', fail)
    if phase == 'register': monkeypatch.setattr(h, 'register_ai_booking', fail)
    if phase == 'marker': monkeypatch.setattr(h, 'record_google_write_start', fail)
    if phase == 'final': monkeypatch.setattr(h, 'mark_booking_created', fail)
    result = asyncio.run(h._execute_confirmed_booking(INTENT, CALENDAR, 'key', 'unused'))
    assert '✅' not in result
    assert env.writes == (1 if phase == 'final' else 0)
    assert env.registrations <= 1


@pytest.mark.parametrize('module_name', ['ai_reschedule_handler', 'ai_deletion_handler'])
def test_move_delete_execute_uses_guard(module_name):
    import importlib
    module = importlib.import_module(module_name)
    tree = ast.parse(Path(module.__file__).read_text())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and n.args
             and isinstance(n.args[0], ast.Name) and n.args[0].id == 'execute']
    assert len(calls) == 1
    assert isinstance(calls[0].func, ast.Name) and calls[0].func.id == 'await_worker'


REAL_TO_THREAD = asyncio.to_thread


@pytest.mark.parametrize('phase', ['register', 'process', 'final'])
def test_creation_cancel_waits_before_releasing_lock(env, monkeypatch, phase):
    monkeypatch.setattr(asyncio, 'to_thread', REAL_TO_THREAD)
    target = {'register': 'register_ai_booking', 'process': 'process_booking',
              'final': 'mark_booking_created'}[phase]
    original = getattr(h, target)
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    def delayed(*args, **kwargs):
        started.set()
        try:
            assert release.wait(2)
            return original(*args, **kwargs)
        finally:
            finished.set()
    monkeypatch.setattr(h, target, delayed)
    async def scenario():
        lock = asyncio.Lock()
        async def handler():
            async with lock:
                await h._execute_confirmed_booking(INTENT, CALENDAR, 'key', 'unused')
        task = asyncio.create_task(handler())
        try:
            for _ in range(500):
                if started.is_set(): break
                await asyncio.sleep(.001)
            assert started.is_set()
            task.cancel()
            await asyncio.sleep(.01)
            assert not task.done() and lock.locked()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError): await task
        assert finished.is_set() and not lock.locked()
    asyncio.run(scenario())
    assert env.writes == (0 if phase == 'register' else 1)
    assert env.registrations == 1
    assert env.row[0] == ('CREATED' if phase == 'final' else 'PENDING')
