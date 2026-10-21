import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "google-calendar-prototype"))

from google_auth import authenticate
from google_calendar import check_availability


def load_calendar_id() -> str:
    env_file = PROJECT_ROOT / ".env"

    for line in env_file.read_text().splitlines():
        key, separator, value = line.strip().partition("=")

        if separator and key.strip() == "GOOGLE_TEST_CALENDAR_ID":
            calendar_id = value.strip()

            if calendar_id and calendar_id != "primary":
                return calendar_id

    raise ValueError("Calendario Google di test non configurato")


def main() -> None:
    calendar_id = load_calendar_id()

    credentials = authenticate()
    service = build("calendar", "v3", credentials=credentials)

    timezone = ZoneInfo("Europe/Rome")

    # Intervallo di un'ora, a partire da domani alle 15:00.
    tomorrow = datetime.now(timezone).date() + timedelta(days=1)

    start = datetime(
        tomorrow.year,
        tomorrow.month,
        tomorrow.day,
        15,
        0,
        tzinfo=timezone,
    )

    available = check_availability(
        service=service,
        calendar_id=calendar_id,
        start=start,
        duration_minutes=60,
    )

    print(f"Data: {start.strftime('%Y-%m-%d')}")
    print("Orario: 15:00–16:00")
    print(f"Disponibilità: {'LIBERO' if available else 'OCCUPATO'}")


if __name__ == "__main__":
    main()