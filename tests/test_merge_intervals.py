import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROTOTYPE_DIR = PROJECT_ROOT / "google-calendar-prototype"

sys.path.insert(0, str(PROTOTYPE_DIR))

from google_calendar import merge_intervals


UTC = ZoneInfo("UTC")


def dt(value):
    return datetime.fromisoformat(value)


def test_overlapping():
    intervals = [
        (
            dt("2026-10-22T15:00:00+02:00"),
            dt("2026-10-22T16:00:00+02:00"),
        ),
        (
            dt("2026-10-22T15:30:00+02:00"),
            dt("2026-10-22T16:30:00+02:00"),
        ),
    ]

    result = merge_intervals(intervals)

    assert result == [
        (
            dt("2026-10-22T13:00:00+00:00"),
            dt("2026-10-22T14:30:00+00:00"),
        )
    ]

    print("TEST SUPERATO: intervalli sovrapposti")


def test_consecutive():
    intervals = [
        (
            dt("2026-10-22T15:00:00+02:00"),
            dt("2026-10-22T16:00:00+02:00"),
        ),
        (
            dt("2026-10-22T16:00:00+02:00"),
            dt("2026-10-22T17:00:00+02:00"),
        ),
    ]

    result = merge_intervals(intervals)

    assert len(result) == 1

    print("TEST SUPERATO: intervalli consecutivi")


def test_different_timezones():
    intervals = [
        (
            dt("2026-10-22T15:00:00+02:00"),
            dt("2026-10-22T16:00:00+02:00"),
        ),
        (
            dt("2026-10-22T13:30:00+00:00"),
            dt("2026-10-22T14:30:00+00:00"),
        ),
    ]

    result = merge_intervals(intervals)

    assert result == [
        (
            dt("2026-10-22T13:00:00+00:00"),
            dt("2026-10-22T14:30:00+00:00"),
        )
    ]

    print("TEST SUPERATO: fusi orari differenti")


def test_empty():
    assert merge_intervals([]) == []

    print("TEST SUPERATO: lista vuota")


def test_invalid_interval():
    intervals = [
        (
            dt("2026-10-22T16:00:00+02:00"),
            dt("2026-10-22T15:00:00+02:00"),
        )
    ]

    try:
        merge_intervals(intervals)
    except ValueError:
        print("TEST SUPERATO: intervallo non valido bloccato")
    else:
        raise AssertionError("Intervallo non valido accettato")


def test_daylight_saving():
    # Il 25 ottobre 2026 termina l'ora legale in Italia.
    # Le 02:30 si verificano due volte, con offset differenti.

    intervals = [
        (
            dt("2026-10-25T02:15:00+02:00"),
            dt("2026-10-25T02:45:00+02:00"),
        ),
        (
            dt("2026-10-25T02:15:00+01:00"),
            dt("2026-10-25T02:45:00+01:00"),
        ),
    ]

    result = merge_intervals(intervals)

    assert len(result) == 2

    print("TEST SUPERATO: cambio ora legale/solare")


if __name__ == "__main__":
    test_overlapping()
    test_consecutive()
    test_different_timezones()
    test_empty()
    test_invalid_interval()
    test_daylight_saving()