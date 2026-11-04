from dataclasses import dataclass
from typing import Literal


Operation = Literal["crea", "modifica", "cancella"]


@dataclass(frozen=True)
class AppointmentIntent:
    """Informazioni estratte da una richiesta di gestione appuntamenti."""

    operazione: Operation
    cliente: str | None = None
    data: str | None = None
    ora: str | None = None
    durata_minuti: int | None = None

    # Utilizzati per identificare l'appuntamento da modificare.
    data_originale: str | None = None
    ora_originale: str | None = None


def validate_operation(value: object) -> Operation:
    """Verifica che l'operazione sia una di quelle consentite."""

    if not isinstance(value, str):
        raise ValueError("L'operazione deve essere una stringa.")

    if value not in ("crea", "modifica", "cancella"):
        raise ValueError(f"Operazione non valida: {value}")

    return value  # type: ignore[return-value]

def validate_optional_string(value: object, field_name: str) -> str | None:
    """Valida un campo testuale opzionale."""

    if value is None:
        return None

    if not isinstance(value, str):
        raise ValueError(f"Il campo '{field_name}' deve essere una stringa.")

    value = value.strip()

    if not value:
        raise ValueError(f"Il campo '{field_name}' non può essere vuoto.")

    return value

def validate_duration(value: object) -> int | None:
    """Valida la durata di un appuntamento espressa in minuti."""

    if value is None:
        return None

    if type(value) is not int:
        raise ValueError("La durata deve essere un numero intero.")

    if value <= 0:
        raise ValueError("La durata deve essere maggiore di zero.")

    return value

def validate_appointment_intent(data: object) -> AppointmentIntent:
    """Valida i dati estratti dall'AI e crea un AppointmentIntent."""

    if not isinstance(data, dict):
        raise ValueError("La risposta deve essere un oggetto JSON.")

    allowed_fields = {
        "operazione",
        "cliente",
        "data",
        "ora",
        "durata_minuti",
        "data_originale",
        "ora_originale",
    }

    unknown_fields = set(data) - allowed_fields

    if unknown_fields:
        raise ValueError(
            f"Campi non riconosciuti: {unknown_fields}"
        )

    return AppointmentIntent(
        operazione=validate_operation(data.get("operazione")),
        cliente=validate_optional_string(data.get("cliente"), "cliente"),
        data=validate_optional_string(data.get("data"), "data"),
        ora=normalize_time(validate_optional_string(data.get("ora"), "ora")),
        durata_minuti=validate_duration(data.get("durata_minuti")),
        data_originale=validate_optional_string(
            data.get("data_originale"), "data_originale"
        ),
        ora_originale=normalize_time(validate_optional_string(
            data.get("ora_originale"), "ora_originale"
        )),
    )

def normalize_time(value: str | None) -> str | None:
    """Normalizza un orario nel formato HH:MM."""

    if value is None:
        return None

    value = value.strip()

    parts = value.split(":")

    if len(parts) == 1:
        parts.append("00")

    if len(parts) != 2:
        raise ValueError(f"Formato orario non valido: {value}")

    try:
        hours = int(parts[0])
        minutes = int(parts[1])
    except ValueError as exc:
        raise ValueError(f"Formato orario non valido: {value}") from exc

    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        raise ValueError(f"Orario non valido: {value}")

    return f"{hours:02d}:{minutes:02d}"