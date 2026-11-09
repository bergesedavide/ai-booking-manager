from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow


BASE_DIR = Path(__file__).resolve().parent

CREDENTIALS_FILE = BASE_DIR / "credentials.json"
TOKEN_FILE = BASE_DIR / "token.json"

SCOPES = [
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    "https://www.googleapis.com/auth/calendar.events.owned",
    "https://www.googleapis.com/auth/calendar.freebusy",
]


def authenticate() -> Credentials:
    """Manual authorization only; never use from the running Telegram bot.

    May open an OAuth browser/server and persist token.json. Bot runtime must
    use google_transport.load_existing_credentials instead.
    """
    credentials = None

    if TOKEN_FILE.exists():
        credentials = Credentials.from_authorized_user_file(
            str(TOKEN_FILE),
            SCOPES,
        )

    if credentials and credentials.valid:
        print("Autenticazione tramite token salvato.")
        return credentials

    if credentials and credentials.expired and credentials.refresh_token:
        print("Rinnovo automatico del token...")
        credentials.refresh(Request())

    else:
        print("Avvio autenticazione Google OAuth...")

        flow = InstalledAppFlow.from_client_secrets_file(
            str(CREDENTIALS_FILE),
            scopes=SCOPES,
        )

        credentials = flow.run_local_server(
            port=0,
            access_type="offline",
            prompt="consent",
        )

    TOKEN_FILE.write_text(credentials.to_json())
    TOKEN_FILE.chmod(0o600)

    return credentials
