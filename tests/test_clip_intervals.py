import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROTOTYPE_DIR = PROJECT_ROOT / "google-calendar-prototype"

sys.path.insert(0, str(PROTOTYPE_DIR))

from google_calendar import clip_intervals


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


WINDOW_START = dt("2026-10-22T15:00:00+02:00")
WINDOW_END = dt("2026-10-22T16:00:00+02:00")


def test_partial_overlap_before():
    intervals = [
        (
            dt("2026-10-22T14:00:00+02:00"),
            dt("2026-10-22T15:30:00+02:00"),
        )
    ]

    result = clip_intervals(
        intervals, WINDOW_START, WINDOW_END
    )

    assert result == [
        (
            dt("2026-10-22T13:00:00+00:00"),
            dt("2026-10-22T13:30:00+00:00"),
        )
    ]

    print("TEST SUPERATO: sovrapposizione iniziale")


def test_partial_overlap_after():
    intervals = [
        (
            dt("2026-10-22T15:30:00+02:00"),
            dt("2026-10-22T17:00:00+02:00"),
        )
    ]

    result = clip_intervals(
        intervals, WINDOW_START, WINDOW_END
    )

    assert result == [
        (
            dt("2026-10-22T13:30:00+00:00"),
            dt("2026-10-22T14:00:00+00:00"),
        )
    ]

    print("TEST SUPERATO: sovrapposizione finale")


def test_event_covers_window():
    intervals = [
        (
            dt("2026-10-22T14:00:00+02:00"),
            dt("2026-10-22T17:00:00+02:00"),
        )
    ]

    result = clip_intervals(
        intervals, WINDOW_START, WINDOW_END
    )

    assert result == [
        (
            dt("2026-10-22T13:00:00+00:00"),
            dt("2026-10-22T14:00:00+00:00"),
        )
    ]

    print("TEST SUPERATO: evento che copre tutta la finestra")


def test_outside_window():
    intervals = [
        (
            dt("2026-10-22T17:00:00+02:00"),
            dt("2026-10-22T18:00:00+02:00"),
        )
    ]

    assert clip_intervals(
        intervals, WINDOW_START, WINDOW_END
    ) == []

    print("TEST SUPERATO: evento esterno escluso")


def test_consecutive_interval():
    intervals = [
        (
            dt("2026-10-22T14:00:00+02:00"),
            dt("2026-10-22T15:00:00+02:00"),
        )
    ]

    assert clip_intervals(
        intervals, WINDOW_START, WINDOW_END
    ) == []

    print("TEST SUPERATO: intervallo consecutivo escluso")


def test_different_timezones():
    intervals = [
        (
            dt("2026-10-22T13:30:00+00:00"),
            dt("2026-10-22T14:30:00+00:00"),
        )
    ]

    result = clip_intervals(
        intervals, WINDOW_START, WINDOW_END
    )

    assert result == [
        (
            dt("2026-10-22T13:30:00+00:00"),
            dt("2026-10-22T14:00:00+00:00"),
        )
    ]

    print("TEST SUPERATO: fusi orari differenti")


def test_invalid_window():
    try:
        clip_intervals(
            [],
            WINDOW_END,
            WINDOW_START,
        )
    except ValueError:
        print("TEST SUPERATO: finestra non valida bloccata")
    else:
        raise AssertionError("Finestra non valida accettata")


def test_empty_intervals():
    assert clip_intervals(
        [], WINDOW_START, WINDOW_END
    ) == []

    print("TEST SUPERATO: lista vuota")


if __name__ == "__main__":
    test_partial_overlap_before()
    test_partial_overlap_after()
    test_event_covers_window()
    test_outside_window()
    test_consecutive_interval()
    test_different_timezones()
    test_invalid_window()
    test_empty_intervals()