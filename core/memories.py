""""On this day": media taken on today's date in earlier years.

Dates are each item's capture time (``file_mtime``, see ``core.media_dates``)
read in the server's local time, the same reading the timeline uses, so a
photo shows up on the day its month and day say.
"""
from __future__ import annotations

import datetime as dt
import math
from collections.abc import Iterable


def _matches(day: dt.date, today: dt.date) -> bool:
    if (day.month, day.day) == (today.month, today.day):
        return True
    # A leap day still comes back in the years without one, on the 28th.
    return (
        (day.month, day.day) == (2, 29)
        and (today.month, today.day) == (2, 28)
        and not _is_leap(today.year)
    )


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def on_this_day(
    items: Iterable[tuple[int, float | None]], today: dt.date
) -> list[tuple[int, list[int]]]:
    """Group ``(index, capture timestamp)`` pairs taken on this date in past years.

    Returns ``(year, indices)`` from the most recent year back; within a year
    the indices keep the time they were taken. Items without a usable date
    are left out, as are items from this year (that is today, not a memory).
    """
    by_year: dict[int, list[tuple[float, int]]] = {}
    for index, stamp in items:
        if not stamp or math.isnan(stamp):
            continue
        try:
            day = dt.datetime.fromtimestamp(stamp).date()
        except (OverflowError, OSError, ValueError):
            continue
        if day.year >= today.year or not _matches(day, today):
            continue
        by_year.setdefault(day.year, []).append((stamp, index))
    return [
        (year, [index for _, index in sorted(entries)])
        for year, entries in sorted(by_year.items(), reverse=True)
    ]
