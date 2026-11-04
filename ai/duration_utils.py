import re


def extract_duration(text: str) -> int | None:
    """Estrae una durata esplicita da un testo italiano."""

    if not isinstance(text, str):
        raise ValueError("Il testo deve essere una stringa.")

    text = text.lower().strip()

    # Non scegliere automaticamente tra durate alternative.
    if re.search(r"\b(?:oppure|o in alternativa|o|massimo|al massimo)\b", text):
        return None

    # Durate composte: "un'ora e 15 minuti", "un ora e 30 min".
    compound_match = re.search(
        r"\bun[' ]ora\s+e\s+(\d+)\s*(?:minuti|minuto|min)\b",
        text,
    )

    if compound_match:
        # Non scegliere automaticamente se compaiono altre durate.
        remaining_text = (
            text[:compound_match.start()]
            + " "
            + text[compound_match.end():]
        )

        if extract_duration(remaining_text) is not None:
            return None

        extra_minutes = int(compound_match.group(1))

        if not 0 < extra_minutes < 60:
            return None

        return 60 + extra_minutes

    # Durate espresse in minuti: "45 minuti", "30 min"
    matches = re.findall(r"\b(\d+)\s*(?:minuti|minuto|min)\b", text)

    if len(matches) > 1:
        return None

    if len(matches) == 1:
        minutes = int(matches[0])
        return minutes if minutes > 0 else None

    # Durate espresse in ore.
    durations = {
        "un'ora e mezza": 90,
        "un ora e mezza": 90,
        "un'ora": 60,
        "un ora": 60,
        "mezz'ora": 30,
        "mezz ora": 30,
        "due ore": 120,
    }

    for expression, minutes in durations.items():
        if expression in text:
            return minutes

    return None