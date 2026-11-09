"""Verifica della disponibilità Google Calendar per l'agente AI.

M06.4: esclusivamente lettura, nessuna modifica agli eventi.
"""

import sys
from pathlib import Path

from googleapiclient.discovery import build

from ai.schemas import AppointmentIntent
from booking_datetime import parse_ai_booking_start

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GOOGLE_DIR = PROJECT_ROOT / "google-calendar-prototype"

if str(GOOGLE_DIR) not in sys.path:
    sys.path.insert(0, str(GOOGLE_DIR))

from google_transport import load_existing_credentials as authenticate
from google_calendar import AvailabilityStatus, check_availability

def check_ai_availability(
    intent: AppointmentIntent,
    calendar_id: str,
) -> AvailabilityStatus:
    """Verifica la disponibilità per una nuova prenotazione AI.

    AVAILABLE: intervallo verificato come libero.
    CONFLICT: intervallo occupato.
    UNKNOWN: disponibilità non verificabile.
    """

    if intent.operazione != "crea":
        return AvailabilityStatus.UNKNOWN

    if not calendar_id or calendar_id == "primary":
        return AvailabilityStatus.UNKNOWN

    if not intent.data or not intent.ora:
        return AvailabilityStatus.UNKNOWN

    if (
        type(intent.durata_minuti) is not int
        or intent.durata_minuti <= 0
    ):
        return AvailabilityStatus.UNKNOWN

    try:
        start = parse_ai_booking_start(intent)

        credentials = authenticate()

        service = build(
            "calendar",
            "v3",
            credentials=credentials,
        )

        available = check_availability(
            service=service,
            calendar_id=calendar_id,
            start=start,
            duration_minutes=intent.durata_minuti,
        )

        return (
            AvailabilityStatus.AVAILABLE
            if available
            else AvailabilityStatus.CONFLICT
        )

    except Exception as error:
        print(
            "ERRORE GOOGLE CALENDAR - Disponibilità: "
            f"{type(error).__name__}: {error}",
            flush=True,
        )
        return AvailabilityStatus.UNKNOWN