"""Tests for dataclass models."""
from datetime import date
from decimal import Decimal

from custom_components.parentpay.models import (
    ArchiveRow,
    Balance,
    Child,
    PaymentItem,
)


def test_child_requires_id_and_name() -> None:
    c = Child(id="11111111", name="Alice")
    assert c.id == "11111111"
    assert c.name == "Alice"


def test_balance_uses_decimal_gbp() -> None:
    b = Balance(child_id="11111111", child_name="Alice", amount=Decimal("36.06"))
    assert b.amount == Decimal("36.06")
    assert b.child_name == "Alice"


def test_payment_item_fields() -> None:
    item = PaymentItem(
        child_id="11111111",
        child_name="Alice",
        payment_item_id="9000001",
        name="Basketball Coaching - Summer Term 2026",
        price=Decimal("100.00"),
        availability="20 Places",
        is_new=True,
    )
    assert item.is_new is True
    assert item.payment_item_id == "9000001"


def test_archive_row_identifies_meal_vs_parent_payment() -> None:
    meal = ArchiveRow(
        child_id="11111111",
        child_name="Alice",
        date_paid=date(2026, 4, 15),
        item="PIZZA SLICE",
        amount_pence=171,
        payment_method="Meal",
        status=None,
        receipt_url=None,
    )
    parent = ArchiveRow(
        child_id="11111111",
        child_name="Alice",
        date_paid=date(2026, 4, 16),
        item="English Macbeth - The Complete Play",
        amount_pence=335,
        payment_method="Parent Account",
        status="Paid",
        receipt_url="https://app.parentpay.com/V3Payer4VBW3/Consumer/PaymentDetailsViewerFX.aspx?TID=1000000001&U=2000000001",
    )
    assert meal.is_meal is True
    assert meal.is_parent_payment is False
    assert parent.is_parent_payment is True
    assert parent.is_meal is False
