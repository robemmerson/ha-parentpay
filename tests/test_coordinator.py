"""Tests for ParentPayCoordinator."""
from __future__ import annotations

from datetime import date, datetime, time
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.parentpay.coordinator import ParentPayCoordinator
from custom_components.parentpay.models import HomeSnapshot


@pytest.fixture
def client() -> AsyncMock:
    c = AsyncMock()
    c.fetch_home = AsyncMock(
        return_value=HomeSnapshot(balances=[], recent_payments=[])
    )
    c.fetch_payment_items = AsyncMock(return_value=[])
    c.fetch_archive = AsyncMock(return_value=[])
    return c


@pytest.fixture
async def coordinator(hass, client: AsyncMock) -> ParentPayCoordinator:
    coord = ParentPayCoordinator(
        hass,
        client=client,
        options={
            "poll_interval_minutes": 30,
            "poll_window_start": "08:00",
            "poll_window_end": "16:00",
            "purchases_list_depth": 10,
        },
    )
    await coord.async_setup()
    return coord


async def test_first_refresh_fetches_home_items_and_archive(
    coordinator: ParentPayCoordinator,
    client: AsyncMock,
) -> None:
    await coordinator._async_update_data()
    assert client.fetch_home.await_count == 1
    assert client.fetch_payment_items.await_count == 1
    assert client.fetch_archive.await_count == 1


async def test_archive_fetch_takes_no_date_arguments(
    coordinator: ParentPayCoordinator,
    client: AsyncMock,
) -> None:
    await coordinator._async_update_data()
    args, kwargs = client.fetch_archive.await_args
    assert args == ()
    assert kwargs == {}


async def test_poll_outside_window_skips_fetch_after_first_run(
    coordinator: ParentPayCoordinator,
    client: AsyncMock,
) -> None:
    # First run (startup) always refreshes
    await coordinator._async_update_data()
    before = client.fetch_home.await_count
    with patch(
        "custom_components.parentpay.coordinator.dt_util.now",
        return_value=datetime.combine(date.today(), time(2, 0)),
    ):
        await coordinator._async_update_data()
    assert client.fetch_home.await_count == before  # no new call


async def test_meals_grouped_by_child_date(
    coordinator: ParentPayCoordinator,
    hass,
) -> None:
    await coordinator.store.async_load()
    # Manually seed two meals on the same day for the same child
    await coordinator.store._meals_store.async_save(
        [
            {
                "hash": "h1",
                "child_id": "11111111",
                "date": "2026-04-15",
                "item": "PIZZA SLICE",
                "amount_pence": 0,
            },
            {
                "hash": "h2",
                "child_id": "11111111",
                "date": "2026-04-15",
                "item": "LUXURY CAKE",
                "amount_pence": -122,
            },
        ]
    )
    await coordinator.store.async_load()
    events = coordinator.meals_for_child("11111111")
    assert len(events) == 1
    (event,) = events
    assert event["date"] == "2026-04-15"
    assert "PIZZA SLICE" in event["summary"]
    assert "LUXURY CAKE" in event["summary"]


async def test_purchases_for_child_newest_first_and_trimmed(
    coordinator: ParentPayCoordinator,
) -> None:
    await coordinator.store._purchases_store.async_save(
        [
            {"hash": "a", "child_id": "11111111", "date": "2026-04-10", "item": "Old", "amount_pence": 100, "receipt_url": None, "completed": False},
            {"hash": "b", "child_id": "11111111", "date": "2026-04-16", "item": "New", "amount_pence": 200, "receipt_url": None, "completed": False},
        ]
    )
    await coordinator.store.async_load()
    coordinator._options["purchases_list_depth"] = 1
    purchases = coordinator.purchases_for_child("11111111")
    assert len(purchases) == 1
    assert purchases[0]["item"] == "New"
