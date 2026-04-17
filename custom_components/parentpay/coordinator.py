"""DataUpdateCoordinator for ParentPay."""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import time, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .client import ParentPayClient
from .const import (
    CONF_POLL_INTERVAL_MIN,
    CONF_POLL_WINDOW_END,
    CONF_POLL_WINDOW_START,
    CONF_PURCHASES_LIST_DEPTH,
    DEFAULT_POLL_INTERVAL_MIN,
    DEFAULT_POLL_WINDOW_END,
    DEFAULT_POLL_WINDOW_START,
    DEFAULT_PURCHASES_LIST_DEPTH,
    DOMAIN,
)
from .exceptions import ParentPayAuthError, ParentPayError
from .store import ParentPayStore

_LOGGER = logging.getLogger(__name__)


class ParentPayCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(
        self,
        hass: HomeAssistant,
        *,
        client: ParentPayClient,
        options: dict[str, Any],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(
                minutes=options.get(CONF_POLL_INTERVAL_MIN, DEFAULT_POLL_INTERVAL_MIN)
            ),
        )
        self._client = client
        self._options = options
        self.store = ParentPayStore(hass)
        self._first_run_done = False

    async def async_setup(self) -> None:
        await self.store.async_load()

    def _in_window(self) -> bool:
        window_start = _parse_hhmm(
            self._options.get(CONF_POLL_WINDOW_START, DEFAULT_POLL_WINDOW_START)
        )
        window_end = _parse_hhmm(
            self._options.get(CONF_POLL_WINDOW_END, DEFAULT_POLL_WINDOW_END)
        )
        now_t = dt_util.now().time()
        return window_start <= now_t <= window_end

    async def _async_update_data(self) -> dict[str, Any]:
        if self._first_run_done and not self._in_window():
            _LOGGER.debug("Skipping poll — outside poll window")
            return self.data or {
                "balances": [],
                "items": [],
                "meals": [],
                "purchases": [],
            }

        try:
            home = await self._client.fetch_home()
            items = await self._client.fetch_payment_items()
            archive_rows = await self._client.fetch_archive()

            # Merge meals from home page + archive into store; both tables produce
            # ArchiveRow instances, and the store dedups via row hash.
            await self.store.async_merge(home.recent_meals)
            await self.store.async_merge(home.recent_payments)
            await self.store.async_merge(archive_rows)

            self._first_run_done = True
            return {
                "balances": home.balances,
                "items": items,
                "meals": self.store.meals,
                "purchases": self.store.purchases,
            }
        except ParentPayAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ParentPayError as err:
            raise UpdateFailed(str(err)) from err

    def meals_for_child(self, child_id: str) -> list[dict[str, Any]]:
        """Return one event dict per (child_id, date) with all item names concatenated."""
        groups: dict[str, list[str]] = defaultdict(list)
        for row in self.store.meals:
            if row["child_id"] != child_id:
                continue
            groups[row["date"]].append(row["item"])
        events: list[dict[str, Any]] = []
        for date_str, items in sorted(groups.items()):
            events.append(
                {
                    "date": date_str,
                    "summary": ", ".join(items),
                }
            )
        return events

    def purchases_for_child(self, child_id: str) -> list[dict[str, Any]]:
        """Return purchases for child, newest-first, capped at purchases_list_depth."""
        depth = self._options.get(CONF_PURCHASES_LIST_DEPTH, DEFAULT_PURCHASES_LIST_DEPTH)
        rows = [r for r in self.store.purchases if r["child_id"] == child_id]
        rows.sort(key=lambda r: r["date"], reverse=True)
        return rows[:depth]


def _parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))
