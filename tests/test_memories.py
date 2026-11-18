"""On this day picks past years' media by month and day, local time."""
from __future__ import annotations

import datetime as dt

from core.memories import on_this_day


def _at(year: int, month: int, day: int, hour: int = 12) -> float:
    return dt.datetime(year, month, day, hour).timestamp()


def test_groups_past_years_newest_first_in_time_order():
    items = [
        (1, _at(2020, 11, 18, 15)),
        (2, _at(2023, 11, 18, 9)),
        (3, _at(2020, 11, 18, 8)),
        (4, _at(2023, 11, 19)),   # another day
        (5, _at(2026, 11, 18)),   # this year: today, not a memory
    ]

    assert on_this_day(items, dt.date(2026, 11, 18)) == [(2023, [2]), (2020, [3, 1])]


def test_items_without_a_date_are_left_out():
    items = [(1, None), (2, 0.0), (3, float("nan")), (4, _at(2024, 3, 1))]

    assert on_this_day(items, dt.date(2026, 3, 1)) == [(2024, [4])]


def test_a_leap_day_returns_on_the_28th_of_other_years():
    items = [(1, _at(2024, 2, 29)), (2, _at(2023, 2, 28))]

    assert on_this_day(items, dt.date(2026, 2, 28)) == [(2024, [1]), (2023, [2])]
    # In a leap year the 29th has its own day.
    assert on_this_day(items, dt.date(2028, 2, 28)) == [(2023, [2])]
    assert on_this_day(items, dt.date(2028, 2, 29)) == [(2024, [1])]


def test_nothing_on_this_day_is_an_empty_list():
    assert on_this_day([(1, _at(2020, 1, 1))], dt.date(2026, 6, 1)) == []
