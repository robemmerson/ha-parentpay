"""Tests for the todo platform."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.todo import TodoItem, TodoItemStatus

from custom_components.parentpay.todo import PurchasesTodoList


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
