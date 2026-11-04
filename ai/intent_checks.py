from ai.schemas import AppointmentIntent


def missing_creation_fields(intent: AppointmentIntent) -> list[str]:
    """Restituisce i campi mancanti per una richiesta di creazione."""

    if intent.operazione != "crea":
        return []

    required_fields = (
        "cliente",
        "data",
        "ora",
        "durata_minuti",
    )

    return [
        field
        for field in required_fields
        if getattr(intent, field) is None
    ]