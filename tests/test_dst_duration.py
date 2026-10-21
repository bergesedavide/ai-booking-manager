from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def test_dst_duration():
    rome = ZoneInfo("Europe/Rome")
    utc = ZoneInfo("UTC")

    # Prima del passaggio all'ora solare
    start = datetime(
        2026, 10, 25, 2, 30,
        tzinfo=rome,
        fold=0,
    )

    duration = timedelta(minutes=60)

    # Calcolo in UTC
    end = (
        start.astimezone(utc) + duration
    ).astimezone(rome)

    # Verifica della durata effettiva
    actual_duration = (
        end.astimezone(utc)
        - start.astimezone(utc)
    )

    assert actual_duration == duration

    assert start.utcoffset() == timedelta(hours=2)
    assert end.utcoffset() == timedelta(hours=1)

    print("TEST SUPERATO: durata di 60 minuti preservata")
    print(f"Inizio: {start.isoformat()}")
    print(f"Fine:   {end.isoformat()}")


if __name__ == "__main__":
    test_dst_duration()