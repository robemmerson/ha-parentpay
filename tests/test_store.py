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


async def test_store_v3_migration_wipes_v2_data(hass, hass_storage) -> None:
    """When STORE_VERSION jumps from 2 to 3, _MigratingStore wipes prior caches."""
    hass_storage["parentpay.meals_v1"] = {
        "version": 2,
        "minor_version": 1,
        "key": "parentpay.meals_v1",
        "data": [
            {
                "hash": "x",
                "child_id": "11111111",
                "date": "2026-01-01",
                "item": "OLD ROW",
                "amount_pence": 100,
            }
        ],
    }
    hass_storage["parentpay.purchases_v1"] = {
        "version": 2,
        "minor_version": 1,
        "key": "parentpay.purchases_v1",
        "data": [
            {
                "hash": "y",
                "child_id": "11111111",
                "date": "2026-01-01",
                "item": "OLD PURCHASE",
                "amount_pence": 200,
                "completed": False,
            }
        ],
    }
    hass_storage["parentpay.payment_details_v1"] = {
        "version": 2,
        "minor_version": 1,
        "key": "parentpay.payment_details_v1",
        "data": {"old-tid": {"tid": "old-tid", "child_id": "11111111"}},
    }

    store = ParentPayStore(hass)  # picks up STORE_VERSION = 3
    await store.async_load()

    assert store.meals == []
    assert store.purchases == []
    assert store.get_payment_detail("old-tid") is None
