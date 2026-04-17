"""Tests for the persistent store wrapper."""
from __future__ import annotations

import hashlib
from datetime import date

import pytest

from custom_components.parentpay.models import ArchiveRow
from custom_components.parentpay.store import ParentPayStore


def _row_hash(r: ArchiveRow) -> str:
    payload = f"{r.child_id}|{r.date_paid.isoformat()}|{r.item}|{r.amount_pence}"
    return hashlib.sha1(payload.encode()).hexdigest()


@pytest.fixture
def store(hass) -> ParentPayStore:
    return ParentPayStore(hass)


async def test_empty_store_returns_empty_lists(store: ParentPayStore) -> None:
    await store.async_load()
    assert store.meals == []
    assert store.purchases == []


async def test_merge_new_rows_deduplicates(store: ParentPayStore) -> None:
    await store.async_load()
    row = ArchiveRow(
        child_id="11111111",
        child_name="Alice",
        date_paid=date(2026, 4, 15),
        item="PIZZA SLICE",
        amount_pence=-171,
        payment_method="Meal",
        status=None,
        receipt_url=None,
    )
    await store.async_merge([row])
    await store.async_merge([row])
    assert len(store.meals) == 1


async def test_merge_preserves_completed_flag_on_existing_purchases(
    store: ParentPayStore,
) -> None:
    await store.async_load()
    row = ArchiveRow(
        child_id="11111111",
        child_name="Alice",
        date_paid=date(2026, 4, 16),
        item="English Macbeth",
        amount_pence=335,
        payment_method="Parent Account",
        status="Paid",
        receipt_url="https://example.com/r/1",
    )
    await store.async_merge([row])
    purchase_uid = _row_hash(row)
    await store.async_set_purchase_completed(purchase_uid, True)
    await store.async_merge([row])
    assert store.purchases[0]["completed"] is True
