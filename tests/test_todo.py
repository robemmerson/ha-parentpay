"""Tests for the todo platform."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.todo import TodoItem, TodoItemStatus

from custom_components.parentpay.models import PaymentItem
from custom_components.parentpay.todo import (
    AvailableToPurchaseTodo,
    PurchasesTodoList,
)


async def test_todo_items_reflect_purchases_sorted_newest_first() -> None:
    coordinator = MagicMock()
    coordinator.purchases_for_child.return_value = [
        {
            "hash": "b",
            "child_id": "11111111",
            "date": "2026-04-16",
            "item": "English Macbeth",
            "amount_pence": 335,
            "receipt_url": "https://example.com/r/2",
            "completed": False,
        },
        {
            "hash": "a",
            "child_id": "11111111",
            "date": "2026-04-10",
            "item": "School Trip",
            "amount_pence": 1200,
            "receipt_url": None,
            "completed": True,
        },
    ]
    coordinator.data = {"balances": [], "items": [], "meals": [], "purchases": []}
    coordinator.store = MagicMock()
    coordinator.store.purchases = []
    entry = MagicMock()
    entry.entry_id = "abc"
    todo = PurchasesTodoList(coordinator, entry, "11111111")
    items = todo.todo_items
    assert items is not None
    assert len(items) == 2
    assert items[0].summary == "English Macbeth"
    assert items[0].status == TodoItemStatus.NEEDS_ACTION
    assert items[1].status == TodoItemStatus.COMPLETED


async def test_update_item_sets_completion_on_store() -> None:
    coordinator = MagicMock()
    coordinator.store = MagicMock()
    coordinator.store.async_set_purchase_completed = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    coordinator.purchases_for_child.return_value = [
        {
            "hash": "a",
            "child_id": "11111111",
            "date": "2026-04-16",
            "item": "X",
            "amount_pence": 0,
            "receipt_url": None,
            "completed": False,
        }
    ]
    coordinator.data = {"balances": [], "items": [], "meals": [], "purchases": []}
    entry = MagicMock()
    entry.entry_id = "abc"
    todo = PurchasesTodoList(coordinator, entry, "11111111")
    await todo.async_update_todo_item(
        TodoItem(summary="X", uid="a", status=TodoItemStatus.COMPLETED)
    )
    coordinator.store.async_set_purchase_completed.assert_awaited_once_with("a", True)


def _payment_item(child_id: str, payment_item_id: str, name: str = "Item") -> PaymentItem:
    from decimal import Decimal
    return PaymentItem(
        child_id=child_id,
        child_name="Alice" if child_id == "11111111" else "Bob",
        payment_item_id=payment_item_id,
        name=name,
        price=Decimal("3.35"),
        availability=None,
        is_new=False,
    )


async def test_available_to_purchase_lists_undismissed_items_only() -> None:
    coordinator = MagicMock()
    coordinator.data = {
        "balances": [],
        "items": [
            _payment_item("11111111", "9000001", "Macbeth"),
            _payment_item("11111111", "9000002", "Calculator"),
            _payment_item("11111111", "9000003", "Locker"),
        ],
        "meals": [],
        "purchases": [],
    }
    coordinator.store = MagicMock()
    coordinator.store.is_dismissed = MagicMock(
        side_effect=lambda c, p: (c, p) == ("11111111", "9000002")
    )
    entry = MagicMock()
    entry.entry_id = "abc"
    todo = AvailableToPurchaseTodo(coordinator, entry, "11111111")
    items = todo.todo_items
    assert items is not None
    summaries = [it.summary for it in items]
    assert summaries == ["Macbeth", "Locker"]
    assert all(it.status == TodoItemStatus.NEEDS_ACTION for it in items)


async def test_available_to_purchase_filters_by_child() -> None:
    coordinator = MagicMock()
    coordinator.data = {
        "balances": [],
        "items": [
            _payment_item("11111111", "9000001"),
            _payment_item("22222222", "9000002"),
        ],
        "meals": [],
        "purchases": [],
    }
    coordinator.store = MagicMock()
    coordinator.store.is_dismissed = MagicMock(return_value=False)
    entry = MagicMock()
    entry.entry_id = "abc"
    todo = AvailableToPurchaseTodo(coordinator, entry, "11111111")
    items = todo.todo_items or []
    assert len(items) == 1
    assert items[0].uid == "9000001"


async def test_tick_dismisses_item() -> None:
    coordinator = MagicMock()
    coordinator.data = {"balances": [], "items": [], "meals": [], "purchases": []}
    coordinator.store = MagicMock()
    coordinator.store.async_set_dismissed = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    entry = MagicMock()
    entry.entry_id = "abc"
    todo = AvailableToPurchaseTodo(coordinator, entry, "11111111")
    await todo.async_update_todo_item(
        TodoItem(summary="X", uid="9000001", status=TodoItemStatus.COMPLETED)
    )
    coordinator.store.async_set_dismissed.assert_awaited_once_with(
        "11111111", "9000001", True
    )


async def test_untick_undismisses_item() -> None:
    coordinator = MagicMock()
    coordinator.data = {"balances": [], "items": [], "meals": [], "purchases": []}
    coordinator.store = MagicMock()
    coordinator.store.async_set_dismissed = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    entry = MagicMock()
    entry.entry_id = "abc"
    todo = AvailableToPurchaseTodo(coordinator, entry, "11111111")
    await todo.async_update_todo_item(
        TodoItem(summary="X", uid="9000001", status=TodoItemStatus.NEEDS_ACTION)
    )
    coordinator.store.async_set_dismissed.assert_awaited_once_with(
        "11111111", "9000001", False
    )
