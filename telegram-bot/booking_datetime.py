"""Validazione condivisa di data e ora per le prenotazioni AI."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ai.schemas import AppointmentIntent


ROME = ZoneInfo("Europe/Rome")


def parse_ai_booking_start(intent: AppointmentIntent) -> datetime:
    """Valida una richiesta AI e restituisce l'inizio in Europe/Rome.

    Solleva ValueError se i dati sono incompleti, non validi
    oppure se l'intervallo attraversa un cambio dell'ora legale.
    """

    if intent.operazione != "crea":
        raise ValueError("Operazione non supportata")

    if not intent.data or not intent.ora:
        raise ValueError("Data e ora obbligatorie")

    if (
        type(intent.durata_minuti) is not int
        or intent.durata_minuti <= 0
    ):
        raise ValueError("Durata non valida")

    try:
        naive_start = datetime.fromisoformat(
            f"{intent.data}T{intent.ora}"
        )
    except ValueError as error:
        raise ValueError("Data o ora non valide") from error

    if naive_start.tzinfo is not None:
        raise ValueError("Fuso orario non previsto")

    # Controlla gli orari inesistenti e ambigui.
    for fold in (0, 1):
        candidate = naive_start.replace(tzinfo=ROME, fold=fold)

        roundtrip = candidate.astimezone(
            timezone.utc
        ).astimezone(ROME)

        if roundtrip.replace(tzinfo=None) != naive_start:
            raise ValueError("Orario inesistente")

    first = naive_start.replace(tzinfo=ROME, fold=0)
    second = naive_start.replace(tzinfo=ROME, fold=1)

    if first.utcoffset() != second.utcoffset():
        raise ValueError("Orario ambiguo")

    start = first

    # Verifica che l'intervallo non attraversi un cambio d'ora.
    naive_end = naive_start + timedelta(
        minutes=intent.durata_minuti
    )

    end = naive_end.replace(tzinfo=ROME)

    for fold in (0, 1):
        candidate = naive_end.replace(tzinfo=ROME, fold=fold)

        roundtrip = candidate.astimezone(
            timezone.utc
        ).astimezone(ROME)

        if roundtrip.replace(tzinfo=None) != naive_end:
            raise ValueError("Fine appuntamento inesistente")

    end_other_fold = naive_end.replace(tzinfo=ROME, fold=1)

    if end.utcoffset() != end_other_fold.utcoffset():
        raise ValueError("Fine appuntamento ambigua")

    if start.utcoffset() != end.utcoffset():
        raise ValueError(
            "Appuntamento attraverso un cambio dell'ora legale"
        )

    return start