"""Tests for parsers."""

from __future__ import annotations

import json
import pathlib
from datetime import date
from decimal import Decimal

import pytest

from custom_components.parentpay.exceptions import ParentPayParseError
from custom_components.parentpay.parsers import (
    extract_receipt_ids,
    parse_archive,
    parse_home_balances,
    parse_home_recent_meals,
    parse_home_recent_payments,
    parse_login_response,
    parse_payment_detail,
    parse_payment_items,
    parse_webforms_state,
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


def test_parse_home_recent_meals_uses_generic_item_label() -> None:
    meals = parse_home_recent_meals(_load_text("balances.html"))
    # Home-page meal table only exposes price, not food name — we use generic
    # labels so downstream code doesn't show "£2.54" as the meal item.
    assert all(m.item in {"School meal", "No meal"} for m in meals)


def test_extract_receipt_ids() -> None:
    url = (
        "https://app.parentpay.com/V3Payer4VBW3/Consumer/"
        "PaymentDetailsViewerFX.aspx?TID=1000000001&U=2000000001"
    )
    assert extract_receipt_ids(url) == ("1000000001", "2000000001")
    assert extract_receipt_ids("https://x?TID=999&U=888") == ("999", "888")
    assert extract_receipt_ids("https://x?U=888&TID=999") == ("999", "888")
    assert extract_receipt_ids("https://x/no-ids") is None


def test_parse_payment_detail_extracts_all_line_items() -> None:
    items = parse_payment_detail(_load_text("payment_detail.html"))
    # Fixture has 2 line items on one receipt
    assert len(items) == 2
    # Both share the same payment_id (the U query param)
    assert {it.payment_id for it in items} == {"2000000001"}
    # TIDs match the receipt-code positional ordering
    assert [it.tid for it in items] == ["1000000001", "1000000002"]
    # First-name match against sidebar data-consumer-data resolves child ids
    assert {it.child_id for it in items} == {"11111111"}
    assert {it.child_name for it in items} == {"Alice"}
    # Full, un-truncated item names
    assert "English Macbeth Bundle" in items[0].item
    assert items[1].item == "English Macbeth - The Complete Play"
    # Amounts as pence
    assert items[0].amount_pence == 1050
    assert items[1].amount_pence == 335
    # Dates parse
    assert items[0].date_paid == date(2026, 4, 16)


def test_parse_archive_handles_full_history_response() -> None:
    """archive_sample.html is the POST response — should hold the full 12-month grid."""
    rows = parse_archive(_load_text("archive_sample.html"))
    assert len(rows) >= 900
    assert {r.child_id for r in rows} >= {"11111111", "22222222"}
    assert {r.payment_method for r in rows} >= {"Meal", "Parent Account"}


def test_parse_webforms_state_extracts_three_tokens() -> None:
    html = """
    <html><body><form>
      <input id="__VIEWSTATE" name="__VIEWSTATE" type="hidden" value="STATEV"/>
      <input id="__VIEWSTATEGENERATOR" name="__VIEWSTATEGENERATOR" type="hidden" value="GENV"/>
      <input id="__EVENTVALIDATION" name="__EVENTVALIDATION" type="hidden" value="EVV"/>
    </form></body></html>
    """
    state = parse_webforms_state(html)
    assert state.viewstate == "STATEV"
    assert state.viewstategenerator == "GENV"
    assert state.eventvalidation == "EVV"


def test_parse_webforms_state_reads_archive_initial_fixture() -> None:
    state = parse_webforms_state(_load_text("archive_initial.html"))
    assert state.viewstate == "TESTVIEWSTATE_INITIAL"
    assert state.viewstategenerator == "TESTGEN_INITIAL"
    assert state.eventvalidation == "TESTEV_INITIAL"


def test_parse_webforms_state_raises_when_token_missing() -> None:
    html = """
    <html><body><form>
      <input id="__VIEWSTATE" name="__VIEWSTATE" type="hidden" value="X"/>
      <input id="__VIEWSTATEGENERATOR" name="__VIEWSTATEGENERATOR" type="hidden" value="Y"/>
    </form></body></html>
    """
    with pytest.raises(ParentPayParseError):
        parse_webforms_state(html)
