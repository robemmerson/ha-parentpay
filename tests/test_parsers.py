"""Tests for parsers."""

from __future__ import annotations

import json
import pathlib
from datetime import date
from decimal import Decimal

import pytest

from custom_components.parentpay.exceptions import ParentPayParseError
from custom_components.parentpay.parsers import (
    parse_archive,
    parse_home_balances,
    parse_home_recent_meals,
    parse_home_recent_payments,
    parse_login_response,
    parse_payment_items,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _load_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _load_json(name: str) -> dict:
    return json.loads(_load_text(name))


def test_parse_login_response_success() -> None:
    payload = _load_json("login_success.json")
    ok, reason = parse_login_response(status=200, payload=payload)
    assert ok is True
    assert reason is None


def test_parse_login_response_bad_credentials() -> None:
    payload = _load_json("login_failure.json")
    ok, reason = parse_login_response(status=400, payload=payload)
    assert ok is False
    assert reason == "Authentication failed."


def test_parse_login_response_rejects_200_without_activation() -> None:
    ok, reason = parse_login_response(status=200, payload={"isActivated": False})
    assert ok is False
    assert reason is not None


def test_parse_login_response_unexpected_shape_raises() -> None:
    with pytest.raises(ParentPayParseError):
        parse_login_response(status=500, payload={"unexpected": "shape"})


def test_parse_home_balances_returns_both_children() -> None:
    balances = parse_home_balances(_load_text("balances.html"))
    by_id = {b.child_id: b for b in balances}
    assert "11111111" in by_id
    assert "22222222" in by_id
    assert by_id["11111111"].child_name == "Alice"
    assert by_id["22222222"].child_name == "Bob"
    assert by_id["11111111"].amount == Decimal("36.06")
    assert by_id["22222222"].amount == Decimal("39.37")


def test_parse_home_recent_meals_picks_up_prices_and_no_meal() -> None:
    meals = parse_home_recent_meals(_load_text("balances.html"))
    assert len(meals) >= 1
    priced = [m for m in meals if m.amount_pence and m.amount_pence > 0]
    no_meal = [m for m in meals if m.amount_pence == 0]
    assert priced, "expected at least one priced meal"
    # "No meal" entries are retained (with zero amount) so downstream code
    # knows the child was at school but did not take a meal
    assert no_meal, "expected at least one No meal entry"
    # Each meal row is tagged with a real ConsumerId
    assert all(m.child_id in {"11111111", "22222222"} for m in meals)


def test_parse_home_recent_payments_returns_parent_account_rows() -> None:
    payments = parse_home_recent_payments(_load_text("balances.html"))
    assert payments, "expected at least one recent payment"
    assert all(p.is_parent_payment for p in payments)
    assert all(p.date_paid <= date(2030, 1, 1) for p in payments)


def test_parse_payment_items_extracts_ids_and_names() -> None:
    items = parse_payment_items(_load_text("payment_items.html"))
    # Every item is tied to one of the two known children
    assert {it.child_id for it in items} <= {"11111111", "22222222"}
    # Child names are resolved from sidebar markup
    assert {it.child_name for it in items} <= {"Alice", "Bob"}
    # Every item has a non-empty payment_item_id and a non-empty name
    assert all(it.payment_item_id for it in items)
    assert all(it.name for it in items)
    # Prices are Decimal-valued
    assert all(it.price is not None for it in items)


def test_parse_archive_extracts_meal_and_parent_account_rows() -> None:
    rows = parse_archive(_load_text("archive_sample.html"))
    assert len(rows) > 0
    meals = [r for r in rows if r.payment_method == "Meal"]
    parent = [r for r in rows if r.payment_method == "Parent Account"]
    assert meals, "expected at least one meal row"
    assert parent, "expected at least one parent-account row"
    # Meal rows carry the meal description in item
    assert any("PIZZA SLICE" in m.item.upper() for m in meals)
    # Parent-account rows have a receipt URL pointing at PaymentDetailsViewerFX
    assert any(p.receipt_url and "PaymentDetailsViewerFX" in p.receipt_url for p in parent)
    # Child ids are the numeric ConsumerIds
    assert {r.child_id for r in rows} <= {"11111111", "22222222"}
    # Dates parse into real date objects
    assert all(isinstance(r.date_paid, date) for r in rows)


def test_parse_archive_initial_get_returns_recent_rows() -> None:
    """Default GET returns only the last ~8 rows but the parser must handle it."""
    rows = parse_archive(_load_text("archive_initial.html"))
    assert 0 < len(rows) <= 20
    assert all(r.child_id.isdigit() for r in rows)


def test_parse_home_recent_payments_is_parent_account_only() -> None:
    rows = parse_home_recent_payments(_load_text("balances.html"))
    # Home-page recent-payments table shows only parent-account rows
    assert all(r.payment_method == "Parent Account" for r in rows)
