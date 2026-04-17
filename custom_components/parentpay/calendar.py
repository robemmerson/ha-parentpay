"""Calendar platform: one calendar per child with a single event per meal-day."""
from __future__ import annotations

import hashlib
from datetime import date, datetime, time
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MANUFACTURER
from .coordinator import ParentPayCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ParentPayCoordinator = hass.data[DOMAIN][entry.entry_id]
    child_ids = {r["child_id"] for r in coordinator.store.meals}
    async_add_entities(
        MealsCalendar(coordinator, entry, child_id) for child_id in sorted(child_ids)
    )


def _child_name_for(coordinator: ParentPayCoordinator, child_id: str) -> str:
    for b in coordinator.data.get("balances", []) or []:
        if b.child_id == child_id:
            return str(b.child_name)
    for it in coordinator.data.get("items", []) or []:
        if it.child_id == child_id:
            return str(it.child_name)
    return child_id


class MealsCalendar(CoordinatorEntity[ParentPayCoordinator], CalendarEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "meals"
    _attr_name = "Meals"

    def __init__(
        self,
        coordinator: ParentPayCoordinator,
        entry: ConfigEntry,
        child_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._child_id = child_id
        self._attr_unique_id = f"{entry.entry_id}_{child_id}_meals"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{child_id}")},
            manufacturer=MANUFACTURER,
            name=_child_name_for(coordinator, child_id),
        )

    @property
    def event(self) -> CalendarEvent | None:
        today = dt_util.now().date()
        for row in self.coordinator.meals_for_child(self._child_id):
            if date.fromisoformat(row["date"]) >= today:
                return _build_event(row, self._child_id)
        return None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        events: list[CalendarEvent] = []
        for row in self.coordinator.meals_for_child(self._child_id):
            d = date.fromisoformat(row["date"])
            if start_date.date() <= d <= end_date.date():
                events.append(_build_event(row, self._child_id))
        return events


def _build_event(row: dict[str, Any], child_id: str) -> CalendarEvent:
    d = date.fromisoformat(row["date"])
    tz = dt_util.get_default_time_zone()
    start = datetime.combine(d, time(12, 0), tzinfo=tz)
    end = datetime.combine(d, time(13, 0), tzinfo=tz)
    uid = hashlib.sha1(f"{child_id}|{row['date']}".encode()).hexdigest()
    return CalendarEvent(
        summary=row["summary"],
        start=start,
        end=end,
        uid=uid,
    )
