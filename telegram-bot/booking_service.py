"""Servizio per la creazione degli appuntamenti su Google Calendar."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
import sys

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROTOTYPE_DIR = PROJECT_ROOT / "google-calendar-prototype"

if str(PROTOTYPE_DIR) not in sys.path:
    sys.path.insert(0, str(PROTOTYPE_DIR))

from google_auth import authenticate
from google_calendar import (
    check_availability,
    create_booking,
    get_booking,
)


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
    force_booking: bool = False,
) -> BookingResult:
    """
    Crea un appuntamento Google Calendar.

    - Controlla la disponibilità, salvo conferma esplicita.
    - Utilizza un ID evento preassegnato quando disponibile.
    - Non ripete automaticamente una scrittura incerta.
    """

    if not calendar_id or calendar_id == "primary":
        return BookingResult(BookingStatus.UNKNOWN)

    if not title or not title.strip():
        return BookingResult(BookingStatus.UNKNOWN)

    if duration_minutes <= 0:
        return BookingResult(BookingStatus.UNKNOWN)

    if not isinstance(start, datetime) or start.tzinfo is None:
        return BookingResult(BookingStatus.UNKNOWN)

    if booking_id is not None:
        if (
            not 5 <= len(booking_id) <= 1024
            or any(
                char not in "0123456789abcdefghijklmnopqrstuv"
                for char in booking_id
            )
        ):
            return BookingResult(BookingStatus.UNKNOWN)

    try:
        credentials = authenticate()
        service = build("calendar", "v3", credentials=credentials)

        if not force_booking:
            available = check_availability(
                service=service,
                calendar_id=calendar_id,
                start=start,
                duration_minutes=duration_minutes,
            )

            if not available:
                return BookingResult(BookingStatus.CONFLICT)

    except Exception as error:
        print(
            "Errore verifica disponibilità: "
            f"{type(error).__name__}"
        )
        return BookingResult(BookingStatus.UNKNOWN)

    # Da questo punto può iniziare una scrittura Google.
    # Non eseguire retry automatici della creazione.
    try:
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
            "Esito creazione Google incerto: "
            f"{type(error).__name__}"
        )

        # Se conosciamo l'ID, possiamo verificare
        # se Google abbia effettivamente creato l'evento.
        if booking_id is not None:
            try:
                event = get_booking(
                    service=service,
                    calendar_id=calendar_id,
                    event_id=booking_id,
                )

                if event.get("id") == booking_id:
                    return BookingResult(
                        status=BookingStatus.CREATED,
                        event_id=booking_id,
                    )

            except HttpError as lookup_error:
                print(
                    "Verifica evento Google non conclusiva: "
                    f"HTTP {lookup_error.resp.status}"
                )

            except Exception as lookup_error:
                print(
                    "Impossibile verificare l'evento: "
                    f"{type(lookup_error).__name__}"
                )

        return BookingResult(BookingStatus.UNKNOWN)