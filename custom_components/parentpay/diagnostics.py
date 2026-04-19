"""Diagnostics for ParentPay — redacts sensitive fields."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_PASSWORD, CONF_USERNAME, DOMAIN
from .coordinator import ParentPayCoordinator

TO_REDACT = {CONF_USERNAME, CONF_PASSWORD, "child_id", "receipt_url"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator: ParentPayCoordinator = hass.data[DOMAIN][entry.entry_id]
    store = coordinator.store
    return {
        "entry": async_redact_data(
            {"data": entry.data, "options": entry.options}, TO_REDACT
        ),
        "store": {
            "meals_count": len(store.meals),
            "purchases_count": len(store.purchases),
            "backfill_done": store.backfill_done,
            "backfill_done_at": store.backfill_done_at,
            "dismissal_count_per_child": store.dismissal_count_per_child(),
        },
    }
