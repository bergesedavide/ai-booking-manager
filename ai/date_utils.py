import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


ROME_TIMEZONE = ZoneInfo("Europe/Rome")

ITALIAN_MONTHS = {
    "gennaio": 1,
    "febbraio": 2,
    "marzo": 3,
    "aprile": 4,
    "maggio": 5,
    "giugno": 6,
    "luglio": 7,
    "agosto": 8,
    "settembre": 9,
    "ottobre": 10,
    "novembre": 11,
    "dicembre": 12,
}

ITALIAN_WEEKDAYS = {
    "lunedì": 0,
    "martedì": 1,
    "mercoledì": 2,
    "giovedì": 3,
    "venerdì": 4,
    "sabato": 5,
    "domenica": 6,
}


def normalize_relative_date(
    value: str | None,
    reference_date: date | None = None,
) -> str | None:
    """Normalizza date italiane nel formato YYYY-MM-DD."""

    if value is None:
        return None

    value = value.strip().lower()

    if not value:
        return None

    if reference_date is None:
        reference_date = datetime.now(ROME_TIMEZONE).date()

    # 1. Date relative.
    offsets = {
        "oggi": 0,
        "domani": 1,
        "dopodomani": 2,
    }

    if value in offsets:
        return (
            reference_date + timedelta(days=offsets[value])
        ).isoformat()

    # 2. Giorni della settimana.
    if value in ITALIAN_WEEKDAYS:
        target_weekday = ITALIAN_WEEKDAYS[value]
        days_ahead = (
            target_weekday - reference_date.weekday()
        ) % 7

        return (reference_date + timedelta(days=days_ahead).isoformat())

    # 3. Date numeriche italiane.
    for date_format in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            parsed_date = datetime.strptime(value, date_format).date()
            return parsed_date.isoformat()
        except ValueError:
            continue

    # 4. Date con mese scritto in italiano.
    match = re.fullmatch(
        r"(\d{1,2})\s+([a-zàèéìòù]+)\s+(\d{4})",
        value,
    )

    if match:
        day = int(match.group(1))
        month_name = match.group(2)
        year = int(match.group(3))

        month = ITALIAN_MONTHS.get(month_name)

        if month is None:
            raise ValueError(
                f"Mese italiano non riconosciuto: {month_name}"
            )

        parsed_date = date(year, month, day)
        return parsed_date.isoformat()

    raise ValueError(
        f"Espressione temporale non supportata: {value}"
    )