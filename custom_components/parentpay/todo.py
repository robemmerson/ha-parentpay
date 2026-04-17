"""Todo platform: per-child parent purchases."""
from __future__ import annotations

from datetime import date
from typing import Any

from homeassistant.components.todo import (  # type: ignore[attr-defined]
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import ParentPayCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ParentPayCoordinator = hass.data[DOMAIN][entry.entry_id]
    child_ids = {r["child_id"] for r in coordinator.store.purchases}
    async_add_entities(
        PurchasesTodoList(coordinator, entry, child_id)
        for child_id in sorted(child_ids)
    )


def _child_name_for(coordinator: ParentPayCoordinator, child_id: str) -> str:
    for b in coordinator.data.get("balances", []) or []:
        if b.child_id == child_id:
            return str(b.child_name)
    for it in coordinator.data.get("items", []) or []:
        if it.child_id == child_id:
            return str(it.child_name)
    return child_id


class PurchasesTodoList(CoordinatorEntity[ParentPayCoordinator], TodoListEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "parent_purchases"
    _attr_name = "Parent purchases"
    _attr_supported_features = TodoListEntityFeature.UPDATE_TODO_ITEM

    def __init__(
        self,
        coordinator: ParentPayCoordinator,
        entry: ConfigEntry,
        child_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._child_id = child_id
        self._attr_unique_id = f"{entry.entry_id}_{child_id}_parent_purchases"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{child_id}")},
            manufacturer=MANUFACTURER,
            name=_child_name_for(coordinator, child_id),
        )

    @property
    def todo_items(self) -> list[TodoItem] | None:
        rows = self.coordinator.purchases_for_child(self._child_id)
        return [
            TodoItem(
                summary=row["item"],
                uid=row["hash"],
                status=(
                    TodoItemStatus.COMPLETED
                    if row.get("completed")
                    else TodoItemStatus.NEEDS_ACTION
                ),
                due=date.fromisoformat(row["date"]),
                description=_build_description(row),
            )
            for row in rows
        ]

    async def async_update_todo_item(self, item: TodoItem) -> None:
        completed = item.status == TodoItemStatus.COMPLETED
        await self.coordinator.store.async_set_purchase_completed(
            item.uid or "", completed
        )
        await self.coordinator.async_request_refresh()


def _build_description(row: dict[str, Any]) -> str:
    amount = int(row.get("amount_pence", 0)) / 100
    parts = [f"\u00a3{amount:.2f}"]
    if row.get("receipt_url"):
        parts.append(row["receipt_url"])
    return " \u00b7 ".join(parts)
