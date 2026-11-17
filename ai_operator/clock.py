from __future__ import annotations

import os
import re
from datetime import date, timedelta

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_SHORT = {d[:3]: i for i, d in enumerate(WEEKDAYS)}


class DateError(ValueError):
    pass


def today() -> date:
    pinned = os.environ.get("OPERATOR_TODAY")
    if pinned:
        return date.fromisoformat(pinned)
    return date.today()


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def pretty(d: date) -> str:
    return d.strftime("%a ") + str(d.day) + d.strftime(" %b %Y")


def _weekday_index(word: str) -> int | None:
    word = word.strip().lower().rstrip(".")
    if word in WEEKDAYS:
        return WEEKDAYS.index(word)
    if word in _SHORT:
        return _SHORT[word]
    return None


def _clean(expr: str) -> str:
    return re.sub(r"\s+", " ", expr.strip().lower().replace("_", " "))


def resolve_date(expr: str, *, base: date | None = None) -> date:
    if not expr or not expr.strip():
        raise DateError("No date given.")
    base = base or today()
    e = _clean(expr)

    try:
        return date.fromisoformat(e)
    except ValueError:
        pass

    if e == "today":
        return base
    if e == "tomorrow":
        return base + timedelta(days=1)
    if e == "yesterday":
        return base - timedelta(days=1)
    if e in ("start of week", "this week", "start of this week", "monday this week"):
        return week_start(base)
    if e in ("start of next week", "next week"):
        return week_start(base) + timedelta(days=7)

    m = re.fullmatch(r"in (\d+) days?", e)
    if m:
        return base + timedelta(days=int(m.group(1)))
    m = re.fullmatch(r"(\d+) days? ago", e)
    if m:
        return base - timedelta(days=int(m.group(1)))

    m = re.fullmatch(r"(?:(next|this|on) )?([a-z]+)", e)
    if m:
        idx = _weekday_index(m.group(2))
        if idx is not None:
            ahead = (idx - base.weekday()) % 7 or 7
            return base + timedelta(days=ahead)

    raise DateError(
        f"Could not understand the date '{expr}'. Use YYYY-MM-DD or words like "
        "'today', 'tomorrow', 'friday', 'in 3 days'."
    )


def resolve_range(expr: str, *, base: date | None = None) -> tuple[date, date]:
    base = base or today()
    e = _clean(expr)
    if e == "today":
        return base, base
    if e == "tomorrow":
        d = base + timedelta(days=1)
        return d, d
    if e == "this week":
        s = week_start(base)
        return s, s + timedelta(days=6)
    if e == "next week":
        s = week_start(base) + timedelta(days=7)
        return s, s + timedelta(days=6)
    if e in ("next 7 days", "the next 7 days", "coming week"):
        return base, base + timedelta(days=6)
    if e == "this month":
        start = base.replace(day=1)
        nxt = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return start, nxt - timedelta(days=1)
    d = resolve_date(expr, base=base)
    return d, d
