"""Date parsing helpers for transaction language."""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta


WEEKDAYS = {name.lower(): index for index, name in enumerate(calendar.day_name)}


def resolve_transaction_date(raw_date: str | None, original_text: str, now: datetime) -> str:
    """Resolve explicit and relative transaction dates into YYYY-MM-DD."""
    text_date = date_from_text(original_text, now.date())
    normalized_raw = str(raw_date or "").strip().lower()
    if text_date and normalized_raw in {"", "today", "none", "null"}:
        return text_date.isoformat()
    if normalized_raw in {"", "today", "none", "null"}:
        return now.date().isoformat()
    raw_relative = date_from_text(normalized_raw, now.date())
    if raw_relative:
        return raw_relative.isoformat()
    return datetime.strptime(normalized_raw, "%Y-%m-%d").date().isoformat()


def date_from_text(text: str, today: date) -> date | None:
    """Find common relative date phrases in natural language."""
    lowered = text.lower()
    if "day before yesterday" in lowered:
        return today - timedelta(days=2)
    if "yesterday" in lowered:
        return today - timedelta(days=1)

    days_match = re.search(r"\b(\d+)\s+days?\s+ago\b", lowered)
    if days_match:
        return today - timedelta(days=int(days_match.group(1)))

    weeks_match = re.search(r"\b(\d+)\s+weeks?\s+ago\b", lowered)
    if weeks_match:
        return today - timedelta(weeks=int(weeks_match.group(1)))

    if "last week" in lowered or "previous week" in lowered or "from last week" in lowered:
        return today - timedelta(days=7)
    if "last month" in lowered or "previous month" in lowered:
        return _shift_month(today, -1)

    for weekday, index in WEEKDAYS.items():
        if f"last {weekday}" in lowered:
            return _previous_weekday(today, index, force_previous=True)
        if re.search(rf"\b{weekday}\b", lowered):
            return _previous_weekday(today, index, force_previous=False)

    return None


def _previous_weekday(today: date, target_weekday: int, force_previous: bool) -> date:
    """Return the most recent matching weekday."""
    delta = (today.weekday() - target_weekday) % 7
    if delta == 0 and force_previous:
        delta = 7
    return today - timedelta(days=delta)


def _shift_month(value: date, months: int) -> date:
    """Move a date by whole months, clamping to the target month's length."""
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)
