"""Dataclass models for ParentPay data."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Child:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class Balance:
    child_id: str      # numeric ParentPay ConsumerId, e.g. "11111111"
    child_name: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class PaymentItem:
    child_id: str
    child_name: str
    payment_item_id: str     # ParentPay numeric item id, e.g. "9000001"
    name: str
    price: Decimal
    availability: str | None
    is_new: bool


@dataclass(frozen=True, slots=True)
class ArchiveRow:
    child_id: str
    child_name: str
    date_paid: date
    item: str
    amount_pence: int
    payment_method: str
    status: str | None
    receipt_url: str | None

    @property
    def is_meal(self) -> bool:
        return self.payment_method == "Meal"

    @property
    def is_parent_payment(self) -> bool:
        return self.payment_method == "Parent Account"


@dataclass(frozen=True, slots=True)
class HomeSnapshot:
    """Result of a single GET against the ParentPay home page."""

    balances: list[Balance]
    recent_meals: list[ArchiveRow]
    recent_payments: list[ArchiveRow]
