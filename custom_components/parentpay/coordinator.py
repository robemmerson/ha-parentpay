"""DataUpdateCoordinator for ParentPay."""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, time, timedelta
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
from .models import ArchiveRow
from .parsers import extract_receipt_ids
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
            await self._maybe_run_backfill()

            home = await self._client.fetch_home()
            items = await self._client.fetch_payment_items()
            archive_rows = await self._client.fetch_archive()

            enriched_payments = await self._enrich_recent_payments(
                home.recent_payments
            )

            await self.store.async_merge(enriched_payments)
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

    async def _maybe_run_backfill(self) -> None:
        """Run the one-shot 12-month backfill if it hasn't succeeded yet.

        On failure, log a warning and leave the done flag unset so the next
        scheduled poll retries the whole sequence. No exponential backoff —
        the poll interval already throttles retries.
        """
        if self.store.backfill_done:
            return
        today = dt_util.now().date()
        start = today - timedelta(days=365)
        try:
            rows = await self._client.fetch_archive_range(start, today)
        except ParentPayError as err:
            _LOGGER.warning(
                "Archive backfill failed, will retry next poll: %s", err
            )
            return
        await self.store.async_merge(rows)
        await self.store.async_mark_backfill_done()

    async def _enrich_recent_payments(
        self, rows: list[ArchiveRow]
    ) -> list[ArchiveRow]:
        """Replace truncated home-page payment rows with enriched detail.

        The home-page "Recent payments" mini-table truncates item names and
        doesn't expose child ids. We resolve each row via the receipt URL
        (which points at PaymentDetailsViewerFX.aspx) and cache the result
        by TID so each transaction is fetched at most once.

        Rows that can't be enriched (no receipt URL, fetch fails, or the
        receipt doesn't list the expected TID) are dropped rather than
        polluting the store with truncated, unassigned entries.
        """
        out: list[ArchiveRow] = []
        for row in rows:
            if not row.receipt_url:
                continue
            ids = extract_receipt_ids(row.receipt_url)
            if ids is None:
                continue
            tid, u = ids
            cached = self.store.get_payment_detail(tid)
            if cached is None:
                try:
                    details = await self._client.fetch_payment_detail(tid, u)
                except ParentPayError as err:
                    _LOGGER.debug(
                        "Failed to enrich payment TID=%s U=%s: %s", tid, u, err
                    )
                    continue
                await self.store.async_store_payment_details(details)
                cached = self.store.get_payment_detail(tid)
            if cached is None or not cached.get("child_id"):
                continue
            out.append(
                ArchiveRow(
                    child_id=str(cached["child_id"]),
                    child_name=str(cached.get("child_name") or ""),
                    date_paid=date.fromisoformat(str(cached["date"])),
                    item=str(cached["item"]),
                    amount_pence=int(cached["amount_pence"]),
                    payment_method="Parent Account",
                    status=cached.get("status"),
                    receipt_url=row.receipt_url,
                )
            )
        return out

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
