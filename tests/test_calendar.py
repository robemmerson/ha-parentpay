"""Tests for the calendar platform."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from custom_components.parentpay.calendar import MealsCalendar


async def test_async_get_events_filters_by_range() -> None:
    coordinator = MagicMock()
    coordinator.meals_for_child.return_value = [
        {"date": "2026-04-14", "summary": "COOKIE"},
        {"date": "2026-04-15", "summary": "PIZZA SLICE, LUXURY CAKE"},
        {"date": "2026-04-16", "summary": "ROLL BUTTER"},
    ]
    coordinator.data = {"balances": [], "items": [], "meals": [], "purchases": []}
    coordinator.store = MagicMock()
    coordinator.store.meals = []
    entry = MagicMock()
    entry.entry_id = "abc"
    cal = MealsCalendar(coordinator, entry, "11111111")
    start = datetime(2026, 4, 15, tzinfo=UTC)
    end = datetime(2026, 4, 16, 23, 59, tzinfo=UTC)
    events = await cal.async_get_events(MagicMock(), start, end)
    assert len(events) == 2
    assert events[0].summary.startswith("PIZZA SLICE")
    assert events[0].start.hour == 12
    assert events[0].end.hour == 13
