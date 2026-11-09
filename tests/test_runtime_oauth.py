"""Offline OAuth runtime contract: simulated credentials/filesystem, no network."""
import ast
import builtins
import importlib.util
import inspect
from pathlib import Path
import socket
import sys
import webbrowser
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'telegram-bot'))
sys.path.insert(0, str(ROOT / 'google-calendar-prototype'))
import google_transport as transport
from google_auth_oauthlib.flow import InstalledAppFlow


@pytest.fixture(autouse=True)
def forbidden_side_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Forbidden network, interactive OAuth or token mutation')
    for name in ('open', 'open_new', 'open_new_tab'):
        monkeypatch.setattr(webbrowser, name, forbidden)
    monkeypatch.setattr(InstalledAppFlow, 'from_client_secrets_file', forbidden)
    monkeypatch.setattr(InstalledAppFlow, 'run_local_server', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'bind', forbidden)
    monkeypatch.setattr(Path, 'write_text', forbidden)
    monkeypatch.setattr(Path, 'write_bytes', forbidden)
    monkeypatch.setattr(Path, 'chmod', forbidden)


@pytest.mark.parametrize('condition', [
    'valid', 'expired', 'missing', 'malformed', 'no_refresh',
    'refresh_failed', 'revoked', 'still_invalid',
])
def test_existing_credentials_only(monkeypatch, condition):
    credentials = Mock(valid=condition == 'valid', refresh_token='fake-refresh')
    loader = Mock(return_value=credentials)
    if condition == 'missing': loader.side_effect = FileNotFoundError('private-secret')
    if condition == 'malformed': loader.side_effect = ValueError('private-secret')
    if condition == 'no_refresh': credentials.refresh_token = None
    def refresh(request):
        assert isinstance(request, transport.BoundedAuthRequest)
        if condition in ('refresh_failed', 'revoked'):
            from google.auth.exceptions import RefreshError
            raise RefreshError('private-secret invalid_grant')
        if condition != 'still_invalid': credentials.valid = True
    credentials.refresh.side_effect = refresh
    monkeypatch.setattr(transport.Credentials, 'from_authorized_user_file', loader)
    if condition in ('valid', 'expired'):
        assert transport.load_existing_credentials() is credentials
    else:
        with pytest.raises(transport.WriteTransportError) as error:
            transport.load_existing_credentials()
        assert str(error.value) == 'Autenticazione locale non disponibile'
        assert 'private-secret' not in str(error.value)
    loader.assert_called_once_with(str(ROOT / 'google-calendar-prototype/token.json'))
    if condition in ('valid', 'missing', 'malformed', 'no_refresh'):
        credentials.refresh.assert_not_called()
    else:
        credentials.refresh.assert_called_once()
    credentials.to_json.assert_not_called()


def test_credential_loader_contract():
    # No invocation of the manual function, which may access real credentials.
    tree = ast.parse((ROOT / 'google-calendar-prototype/google_auth.py').read_text())
    manual = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'authenticate')
    assert not manual.args.args and not manual.args.kwonlyargs
    assert not inspect.signature(transport.load_existing_credentials).parameters
    assert transport.Credentials.__name__ == 'Credentials'


def test_bot_import_does_not_depend_on_interactive_auth(monkeypatch):
    original_import = builtins.__import__
    def guarded_import(name, *args, **kwargs):
        if name == 'google_auth':
            pytest.fail('Telegram runtime imported manual OAuth authentication')
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded_import)
    original_read = Path.read_text
    def read(path, *args, **kwargs):
        if path == ROOT / '.env':
            return ('TELEGRAM_BOT_TOKEN=fake-token\nTELEGRAM_ALLOWED_USER_ID=1\n'
                    'GOOGLE_TEST_CALENDAR_ID=test-calendar\nPOSTGRES_PASSWORD=fake-password\n')
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read)
    spec = importlib.util.spec_from_file_location('oauth_contract_bot', ROOT / 'telegram-bot/bot.py')
    bot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bot)  # Imports only: never main()/polling.
    assert bot.authenticate is transport.load_existing_credentials


def test_ai_clients_keep_noninteractive_factory():
    import availability_service
    import booking_service
    import ai_reschedule_service
    import ai_deletion_handler
    assert availability_service.authenticate is transport.load_existing_credentials
    assert booking_service.authenticate is transport.load_existing_credentials
    assert booking_service.build is transport.build_calendar_service
    assert ai_reschedule_service.authenticate is transport.load_existing_credentials
    assert ai_reschedule_service.build is transport.build_calendar_service
    assert ai_deletion_handler.google_service is ai_reschedule_service.google_service
