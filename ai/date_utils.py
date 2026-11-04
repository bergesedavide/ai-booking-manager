from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


ROME_TIMEZONE = ZoneInfo("Europe/Rome")


def normalize_relative_date(
    value: str | None,
    reference_date: date | None = None,
) -> str | None:
    """Normalizza le date relative nel formato YYYY-MM-DD."""

    if value is None:
        return None

    value = value.strip().lower()

    if reference_date is None:
        reference_date = datetime.now(ROME_TIMEZONE).date()

    offsets = {
        "oggi": 0,
        "domani": 1,
        "dopodomani": 2,
    }

    if value in offsets:
        result = reference_date + timedelta(days=offsets[value])
        return result.isoformat()

    weekdays = {
        "lunedì": 0,
        "martedì": 1,
        "mercoledì": 2,
        "giovedì": 3,
        "venerdì": 4,
        "sabato": 5,
        "domenica": 6,
    }

    if value in weekdays:
        target_weekday = weekdays[value]
        current_weekday = reference_date.weekday()

        days_ahead = (target_weekday - current_weekday) % 7

        result = reference_date + timedelta(days=days_ahead)
        return result.isoformat()

        # Date esplicite nel formato italiano.
    for date_format in ("%d-%m-%Y", "%d/%m/%Y"):
        try:
            parsed_date = datetime.strptime(value, date_format).date()
            return parsed_date.isoformat()
        except ValueError:
            continue

    raise ValueError(f"Espressione temporale non supportata: {value}")