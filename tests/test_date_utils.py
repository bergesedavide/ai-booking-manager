"""Offline date regressions with fixed reference dates; no services or threads."""
from datetime import date, datetime
import json
from unittest.mock import Mock, patch

import pytest

from ai.date_utils import normalize_relative_date, ROME_TIMEZONE
from ai.interpreter import AppointmentInterpreter


WEEKDAYS = ('lunedì', 'martedì', 'mercoledì', 'giovedì', 'venerdì', 'sabato', 'domenica')
# Explicit calendar expectations: rows are reference days, columns target weekdays.
WEEK_CASES = (
    ('2026-11-09', (9, 10, 11, 12, 13, 14, 15)),
    ('2026-11-10', (16, 10, 11, 12, 13, 14, 15)),
    ('2026-11-11', (16, 17, 11, 12, 13, 14, 15)),
    ('2026-11-12', (16, 17, 18, 12, 13, 14, 15)),
    ('2026-11-13', (16, 17, 18, 19, 13, 14, 15)),
    ('2026-11-14', (16, 17, 18, 19, 20, 14, 15)),
    ('2026-11-15', (16, 17, 18, 19, 20, 21, 15)),
)


@pytest.mark.parametrize('reference,weekday,expected', [
    (reference, weekday, f'2026-11-{day:02d}')
    for reference, days in WEEK_CASES
    for weekday, day in zip(WEEKDAYS, days)
])
def test_weekdays_including_current_day_and_next_week(reference, weekday, expected):
    assert normalize_relative_date(weekday, date.fromisoformat(reference)) == expected


@pytest.mark.parametrize('reference,weekday,expected', [
    ('2026-11-30', 'martedì', '2026-12-01'),
    ('2026-01-31', 'lunedì', '2026-02-02'),
    ('2026-12-31', 'venerdì', '2027-01-01'),
    ('2026-12-31', 'lunedì', '2027-01-04'),
    ('2028-02-28', 'martedì', '2028-02-29'),
    ('2028-02-29', 'mercoledì', '2028-03-01'),
])
def test_month_year_and_leap_year_boundaries(reference, weekday, expected):
    assert normalize_relative_date(weekday, date.fromisoformat(reference)) == expected


@pytest.mark.parametrize('value,expected', [
    ('2026-11-20', '2026-11-20'),
    ('20/11/2026', '2026-11-20'),
    ('20-11-2026', '2026-11-20'),
    ('20 novembre 2026', '2026-11-20'),
    ('2028-02-29', '2028-02-29'),
    ('oggi', '2026-12-31'),
    ('domani', '2027-01-01'),
    ('dopodomani', '2027-01-02'),
    ('  VENERDÌ  ', '2027-01-01'),
    (None, None),
    ('  ', None),
])
def test_existing_inputs_unchanged(value, expected):
    assert normalize_relative_date(value, date(2026, 12, 31)) == expected


@pytest.mark.parametrize('value', ['2026-02-29', '31 novembre 2026', 'giorno sconosciuto'])
def test_invalid_inputs_still_rejected(value):
    with pytest.raises(ValueError):
        normalize_relative_date(value, date(2026, 11, 9))


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        assert tz == ROME_TIMEZONE
        return cls(2026, 11, 9, 12, tzinfo=tz)


def test_default_reference_uses_rome_date():
    with patch('ai.date_utils.datetime', FixedDateTime):
        assert normalize_relative_date('lunedì') == '2026-11-09'
        assert normalize_relative_date('venerdì') == '2026-11-13'


@pytest.mark.parametrize('operation', ['crea', 'modifica', 'cancella'])
@pytest.mark.parametrize('relative', [True, False])
def test_interpreter_date_normalization_for_all_ai_operations(operation, relative):
    data = {
        'operazione': operation,
        'cliente': 'Cliente fittizio',
        'data': 'sabato' if relative else '2026-11-14',
        'ora': '16:00',
        'durata_minuti': 30 if operation == 'crea' else None,
        'data_originale': ('venerdì' if relative else '2026-11-13') if operation == 'modifica' else None,
        'ora_originale': '15:00' if operation == 'modifica' else None,
    }
    client = Mock()
    client.chat.return_value = json.dumps(data)
    with patch('ai.interpreter.datetime', FixedDateTime):
        intent = AppointmentInterpreter(client).interpret('Richiesta fittizia')
    assert intent.operazione == operation
    assert intent.data == '2026-11-14'
    assert intent.data_originale == ('2026-11-13' if operation == 'modifica' else None)
    assert intent.cliente == data['cliente']
    assert intent.ora == data['ora']
    assert intent.ora_originale == data['ora_originale']
    assert intent.durata_minuti == data['durata_minuti']
    client.chat.assert_called_once()
