"""Servizio condiviso per la creazione degli appuntamenti.

M06.5: verifica disponibilità e creazione su Google Calendar.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
import sys

from googleapiclient.discovery import build

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROTOTYPE_DIR = PROJECT_ROOT / "google-calendar-prototype"

if str(PROTOTYPE_DIR) not in sys.path:
    sys.path.insert(0, str(PROTOTYPE_DIR))

from google_auth import authenticate
from google_calendar import check_availability, create_booking


class BookingStatus(str, Enum):
    CREATED = "CREATED"
    CONFLICT = "CONFLICT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class BookingResult:
    status: BookingStatus
    event_id: str | None = None


def process_booking(
    calendar_id: str,
    title: str,
    start: datetime,
    duration_minutes: int,
    booking_id: str | None = None,
) -> BookingResult:
    """Verifica la disponibilità e crea l'evento soltanto se libero."""

    # Validazione prima di qualsiasi chiamata Google.
    if not calendar_id or calendar_id == "primary":
        return BookingResult(BookingStatus.UNKNOWN)

    if not title.strip():
        return BookingResult(BookingStatus.UNKNOWN)

    if duration_minutes <= 0:
        return BookingResult(BookingStatus.UNKNOWN)

    if start.tzinfo is None:
        return BookingResult(BookingStatus.UNKNOWN)

    try:
        credentials = authenticate()
        service = build("calendar", "v3", credentials=credentials)

        available = check_availability(
            service=service,
            calendar_id=calendar_id,
            start=start,
            duration_minutes=duration_minutes,
        )

        if not available:
            return BookingResult(BookingStatus.CONFLICT)

        event = create_booking(
            service=service,
            calendar_id=calendar_id,
            title=title,
            start=start,
            duration_minutes=duration_minutes,
            booking_id=booking_id,
        )

        event_id = event.get("id")

        if not event_id:
            return BookingResult(BookingStatus.UNKNOWN)

        return BookingResult(
            status=BookingStatus.CREATED,
            event_id=event_id,
        )

    except Exception as error:
        print(
            "Errore durante la prenotazione: "
            f"{type(error).__name__}"
        )
        return BookingResult(BookingStatus.UNKNOWN)