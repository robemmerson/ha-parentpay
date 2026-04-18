"""Persistent store for meal history and parent purchases."""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    STORE_KEY_MEALS,
    STORE_KEY_PAYMENT_DETAILS,
    STORE_KEY_PURCHASES,
    STORE_VERSION,
)
from .models import ArchiveRow, PaymentDetailItem

_LOGGER = logging.getLogger(__name__)


def row_hash(row: ArchiveRow) -> str:
    payload = f"{row.child_id}|{row.date_paid.isoformat()}|{row.item}|{row.amount_pence}"
    return hashlib.sha1(payload.encode()).hexdigest()


class ParentPayStore:
    """Owns meals + purchases persisted JSON."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._meals_store: Store[list[dict[str, Any]]] = Store(
            hass, STORE_VERSION, STORE_KEY_MEALS
        )
        self._purchases_store: Store[list[dict[str, Any]]] = Store(
            hass, STORE_VERSION, STORE_KEY_PURCHASES
        )
        self._payment_details_store: Store[dict[str, dict[str, Any]]] = Store(
            hass, STORE_VERSION, STORE_KEY_PAYMENT_DETAILS
        )
        self._meals: list[dict[str, Any]] = []
        self._purchases: list[dict[str, Any]] = []
        self._payment_details: dict[str, dict[str, Any]] = {}
        self._loaded = False

    async def async_load(self) -> None:
        self._meals = (await self._meals_store.async_load()) or []
        self._purchases = (await self._purchases_store.async_load()) or []
        self._payment_details = (
            await self._payment_details_store.async_load()
        ) or {}
        self._loaded = True

    def get_payment_detail(self, tid: str) -> dict[str, Any] | None:
        """Return the cached detail for a TID, or None if not yet fetched."""
        return self._payment_details.get(tid)

    async def async_store_payment_details(
        self, items: list[PaymentDetailItem]
    ) -> None:
        """Cache all line items from a receipt fetch, keyed by TID."""
        if not self._loaded:
            await self.async_load()
        for it in items:
            if not it.tid:
                continue
            self._payment_details[it.tid] = {
                "tid": it.tid,
                "payment_id": it.payment_id,
                "child_id": it.child_id,
                "child_name": it.child_name,
                "item": it.item,
                "amount_pence": it.amount_pence,
                "date": it.date_paid.isoformat(),
                "status": it.status,
            }
        await self._payment_details_store.async_save(self._payment_details)

    @property
    def meals(self) -> list[dict[str, Any]]:
        return list(self._meals)

    @property
    def purchases(self) -> list[dict[str, Any]]:
        return list(self._purchases)

    async def async_merge(self, rows: list[ArchiveRow]) -> None:
        """Merge new rows into the store, preserving existing `completed` flags."""
        if not self._loaded:
            await self.async_load()

        meal_hashes = {r["hash"] for r in self._meals}
        purchase_hashes = {r["hash"]: r for r in self._purchases}

        for row in rows:
            h = row_hash(row)
            payload: dict[str, Any] = {
                "hash": h,
                "child_id": row.child_id,
                "date": row.date_paid.isoformat(),
                "item": row.item,
                "amount_pence": row.amount_pence,
            }
            if row.is_meal:
                if h not in meal_hashes:
                    self._meals.append(payload)
                    meal_hashes.add(h)
            elif row.is_parent_payment:
                payload["receipt_url"] = row.receipt_url
                existing = purchase_hashes.get(h)
                payload["completed"] = existing.get("completed", False) if existing else False
                if existing:
                    idx = self._purchases.index(existing)
                    self._purchases[idx] = payload
                else:
                    self._purchases.append(payload)
                purchase_hashes[h] = payload

        await self._meals_store.async_save(self._meals)
        await self._purchases_store.async_save(self._purchases)

    async def async_set_purchase_completed(self, uid: str, completed: bool) -> None:
        if not self._loaded:
            await self.async_load()
        for row in self._purchases:
            if row["hash"] == uid:
                row["completed"] = completed
                await self._purchases_store.async_save(self._purchases)
                return
        _LOGGER.warning(
            "Purchase uid %s not found when setting completed=%s", uid, completed
        )
