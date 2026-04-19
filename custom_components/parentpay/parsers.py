"""Pure-function parsers for ParentPay responses."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from bs4 import BeautifulSoup

from .exceptions import ParentPayParseError
from .models import (
    ArchiveRow,
    Balance,
    PaymentDetailItem,
    PaymentItem,
    WebFormsState,
)

_BALANCE_SPAN_RE = re.compile(r"£\s*(?P<amount>\d+\.\d{2})")


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def parse_login_response(*, status: int, payload: Mapping[str, Any]) -> tuple[bool, str | None]:
    """Classify a login API response.

    Success: HTTP 200 and payload.isActivated is True.
    Failure: HTTP 400 (bad credentials) or HTTP 200 with isActivated != True.
    Any other combination raises ParentPayParseError — the caller shouldn't
    guess.
    """
    if status == 200 and payload.get("isActivated") is True:
        return True, None
    if status == 400 and isinstance(payload.get("message"), str):
        return False, payload["message"]
    if status == 200:
        return False, "Login response reports account not activated"
    raise ParentPayParseError(
        f"Unexpected login response: status={status} payload_keys={sorted(payload)}"
    )


def _build_child_name_map(soup: BeautifulSoup) -> dict[str, str]:
    """ConsumerId → name, from sidebar data-consumer-data attributes."""
    out: dict[str, str] = {}
    for el in soup.find_all(attrs={"data-consumer-data": True}):
        try:
            raw = str(el.get("data-consumer-data") or "").replace("'", '"')
            obj = json.loads(raw)
        except (ValueError, KeyError):
            continue
        cid = str(obj.get("id") or "").strip()
        name = str(obj.get("name") or "").strip()
        if cid and name:
            out[cid] = name
    return out


def parse_home_balances(html: str) -> list[Balance]:
    """Extract per-child dinner-money balance from the Default.aspx home page."""
    soup = _soup(html)
    name_by_id = _build_child_name_map(soup)
    balances: list[Balance] = []
    # Balance span id IS the ConsumerId
    for span in soup.select("span.large.text-primary[id]"):
        cid = str(span.get("id", "")).strip()
        if not cid or not cid.isdigit():
            continue
        m = _BALANCE_SPAN_RE.search(span.get_text(" ", strip=True))
        if not m:
            continue
        balances.append(
            Balance(
                child_id=cid,
                child_name=name_by_id.get(cid, cid),
                amount=Decimal(m.group("amount")),
            )
        )
    if not balances:
        raise ParentPayParseError("No balances found on home page", snippet=html[:500])
    return balances


def parse_home_recent_payments(html: str) -> list[ArchiveRow]:
    """Extract the 'Recent payments' table from the home (Default.aspx) page.

    The home-page table has a different structure from the archive page — it
    shows 4 columns (Date, Type, Details, Amount) with no per-row consumer ID.
    All rows in this table are Parent Account transactions.
    """
    soup = _soup(html)
    rows: list[ArchiveRow] = []
    # Locate the "Recent payments" table by its summary attribute
    table = soup.find("table", attrs={"summary": "Recent payments"})
    if table is None:
        return rows
    for tr in table.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 4:
            continue
        date_text = cells[0].get_text(" ", strip=True)
        amount_text = cells[3].get_text(" ", strip=True)
        try:
            the_date = _parse_short_date(date_text)
        except ParentPayParseError:
            continue
        # Item name from the anchor's data-gtm-label or link text
        receipt_a = cells[2].find("a", href=re.compile(r"PaymentDetailsViewerFX\.aspx"))
        if receipt_a:
            item_name = str(receipt_a.get("data-gtm-label") or receipt_a.get_text(" ", strip=True))
            receipt_url = str(receipt_a["href"])
        else:
            item_name = cells[2].get_text(" ", strip=True)
            receipt_url = None
        rows.append(
            ArchiveRow(
                child_id="",
                child_name="",
                date_paid=the_date,
                item=item_name,
                amount_pence=_amount_to_pence(amount_text),
                payment_method="Parent Account",
                status=None,
                receipt_url=receipt_url,
            )
        )
    return rows


_DATE_RE = re.compile(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{2,4})")
_MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}


def _parse_short_date(text: str) -> date:
    m = _DATE_RE.search(text)
    if not m:
        raise ParentPayParseError(f"Unparseable date: {text!r}")
    day = int(m.group(1))
    month_key = m.group(2).title()
    if month_key not in _MONTHS:
        raise ParentPayParseError(f"Unknown month in {text!r}")
    year = int(m.group(3))
    if year < 100:
        year += 2000
    return date(year, _MONTHS[month_key], day)


def _amount_to_pence(text: str) -> int:
    cleaned = text.replace("£", "").replace(",", "").strip()
    if not cleaned:
        return 0
    return round(Decimal(cleaned) * 100)


def _parse_archive_rows(html: str, *, only_parent_account: bool = False) -> list[ArchiveRow]:
    """Parse <tr cid="..."> rows from either home-page or archive-page markup.

    Used by parse_home_recent_payments (only_parent_account=True) and
    parse_archive (only_parent_account=False).
    """
    soup = _soup(html)
    name_by_id = _build_child_name_map(soup)
    rows: list[ArchiveRow] = []
    for tr in soup.find_all("tr", attrs={"cid": True}):
        cid = str(tr.get("cid") or "").strip()
        if not cid.isdigit() or int(cid) == 0:
            continue
        cells = tr.find_all("td")
        if len(cells) < 7:
            continue
        item_name = cells[1].get_text(" ", strip=True)
        date_text = cells[2].get_text(" ", strip=True)
        meal_desc = cells[4].get_text(" ", strip=True)
        status_text = cells[5].get_text(" ", strip=True)
        amount_text = cells[6].get_text(" ", strip=True)
        try:
            the_date = _parse_short_date(date_text)
        except ParentPayParseError:
            continue
        is_meal = bool(meal_desc)
        if only_parent_account and is_meal:
            continue
        payment_method = "Meal" if is_meal else "Parent Account"
        effective_item = meal_desc if is_meal else item_name
        receipt_a = tr.find("a", href=re.compile(r"PaymentDetailsViewerFX\.aspx"))
        receipt_url = str(receipt_a["href"]) if receipt_a else None
        rows.append(
            ArchiveRow(
                child_id=cid,
                child_name=name_by_id.get(cid, cid),
                date_paid=the_date,
                item=effective_item,
                amount_pence=_amount_to_pence(amount_text),
                payment_method=payment_method,
                status=status_text or None,
                receipt_url=receipt_url,
            )
        )
    return rows


def parse_archive(html: str) -> list[ArchiveRow]:
    """Parse the payment-history (archive) page into ArchiveRow instances."""
    rows = _parse_archive_rows(html, only_parent_account=False)
    if not rows:
        raise ParentPayParseError("No archive rows parsed", snippet=html[:500])
    return rows


_PRICE_RE = re.compile(r"£\s*(?P<amount>-?\d+(?:\.\d{2})?)")


def _first_text(el: Any) -> str:
    return el.get_text(" ", strip=True) if el is not None else ""


def parse_payment_items(html: str) -> list[PaymentItem]:
    """Parse the Payment Items page into per-child PaymentItem rows."""
    soup = _soup(html)
    name_by_id = _build_child_name_map(soup)
    results: list[PaymentItem] = []
    for container in soup.select("div.well.payment-item"):
        ids_el = container.find(attrs={"data-payment-item-id": True})
        if ids_el is None:
            continue
        raw_item_id = ids_el.get("data-payment-item-id") or ""
        raw_cid = ids_el.get("data-consumer-id") or ""
        payment_item_id = (raw_item_id if isinstance(raw_item_id, str) else raw_item_id[0]).strip()
        child_id = (raw_cid if isinstance(raw_cid, str) else raw_cid[0]).strip()
        if not payment_item_id or not child_id:
            continue
        # Item name: inside div.payment-item-list > dl > dd.large
        name_el = container.select_one("div.payment-item-list dd.large")
        name = _first_text(name_el)
        # Price: span inside the [data-payment-item-id] div
        price_el = ids_el.select_one("span.text-nowrap.large.text-primary")
        cost_text = _first_text(price_el) if price_el is not None else _first_text(ids_el)
        price_match = _PRICE_RE.search(cost_text)
        price = Decimal(price_match.group("amount")) if price_match else Decimal("0")
        availability_label = container.find(string=lambda s: s and "Availability" in s)
        availability: str | None = None
        if availability_label:
            sibling = availability_label.find_next(string=True)
            availability = sibling.strip() if sibling else None
        is_new = bool(
            container.find(string=lambda s: s and s.strip() == "New!")
            or container.select_one(".badge-new, .new-badge")
        )
        results.append(
            PaymentItem(
                child_id=child_id,
                child_name=name_by_id.get(child_id, child_id),
                payment_item_id=payment_item_id,
                name=name,
                price=price,
                availability=availability,
                is_new=is_new,
            )
        )
    if not results:
        raise ParentPayParseError("No payment items found", snippet=html[:500])
    return results


# --- PaymentDetailsViewerFX.aspx receipt page ------------------------------

_RECEIPT_CODE_RE = re.compile(r"PP(\d+)")
_DETAIL_DATE_RE = re.compile(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})")
_RECEIPT_URL_RE = re.compile(
    r"[?&]TID=(?P<tid>\d+).*?[?&]U=(?P<u>\d+)"
    r"|[?&]U=(?P<u2>\d+).*?[?&]TID=(?P<tid2>\d+)"
)


def extract_receipt_ids(url: str) -> tuple[str, str] | None:
    """Return (tid, u) from a PaymentDetailsViewerFX URL, or None if unparseable."""
    m = _RECEIPT_URL_RE.search(url)
    if not m:
        return None
    tid = m.group("tid") or m.group("tid2") or ""
    u = m.group("u") or m.group("u2") or ""
    if not tid or not u:
        return None
    return tid, u


def _parse_detail_date(text: str) -> date:
    m = _DETAIL_DATE_RE.search(text)
    if not m:
        raise ParentPayParseError(f"Unparseable date: {text!r}")
    day = int(m.group(1))
    month_key = m.group(2).title()
    if month_key not in _MONTHS:
        raise ParentPayParseError(f"Unknown month in {text!r}")
    return date(int(m.group(3)), _MONTHS[month_key], day)


def parse_payment_detail(html: str) -> list[PaymentDetailItem]:
    """Parse a PaymentDetailsViewerFX.aspx receipt page into line items.

    One receipt can list multiple line items — each gets its own TID, and the
    `PP{TID}` receipt codes are in the same positional order as the rows.
    """
    soup = _soup(html)
    name_by_id = _build_child_name_map(soup)
    # Map first name → consumer id for fuzzy lookup ("Lauren Emmerson" → "18416154")
    first_name_to_cid = {name.split()[0].lower(): cid for cid, name in name_by_id.items()}

    # Payment ID (the `U` query param, shared across all line items)
    payment_id = ""
    for dt_el in soup.find_all("dt"):
        if dt_el.get_text(strip=True) == "Payment ID":
            dd = dt_el.find_next("dd")
            if dd is not None:
                payment_id = dd.get_text(strip=True)
            break

    # Receipt codes → per-line TIDs in row order
    tids: list[str] = []
    for dt_el in soup.find_all("dt"):
        if dt_el.get_text(strip=True) == "Receipt code":
            dd = dt_el.find_next("dd")
            if dd is not None:
                tids = _RECEIPT_CODE_RE.findall(dd.get_text(" ", strip=True))
            break

    # Line-items table: <table summary="Account statements" class="...tbHistoryDesktop">
    table = None
    for t in soup.find_all("table"):
        classes: list[str] = list(t.get("class") or [])
        if t.get("summary") == "Account statements" and "tbHistoryDesktop" in classes:
            table = t
            break
    if table is None:
        raise ParentPayParseError(
            "No line-items table found on receipt page", snippet=html[:500]
        )

    items: list[PaymentDetailItem] = []
    body_rows = [tr for tr in table.find_all("tr") if tr.find("td")]
    for idx, tr in enumerate(body_rows):
        cells = tr.find_all("td")
        if len(cells) < 7:
            continue
        date_text = cells[0].get_text(" ", strip=True)
        pupil_name = cells[1].get_text(" ", strip=True)
        item_name = cells[2].get_text(" ", strip=True)
        amount_text = cells[5].get_text(" ", strip=True)
        status_text = cells[6].get_text(" ", strip=True)
        try:
            the_date = _parse_detail_date(date_text)
        except ParentPayParseError:
            continue
        first_name = pupil_name.split()[0] if pupil_name else ""
        cid = first_name_to_cid.get(first_name.lower(), "")
        tid = tids[idx] if idx < len(tids) else ""
        items.append(
            PaymentDetailItem(
                tid=tid,
                payment_id=payment_id,
                child_id=cid,
                child_name=name_by_id.get(cid, first_name),
                item=item_name,
                amount_pence=_amount_to_pence(amount_text),
                date_paid=the_date,
                status=status_text or None,
            )
        )
    if not items:
        raise ParentPayParseError(
            "No line items parsed from receipt page", snippet=html[:500]
        )
    return items


def parse_webforms_state(html: str) -> WebFormsState:
    """Extract __VIEWSTATE, __VIEWSTATEGENERATOR, __EVENTVALIDATION from a WebForms page.

    All three tokens must be present — the search POST will be rejected without
    them — so a missing token is a parse error, not an empty-default.
    """
    soup = _soup(html)
    fields: dict[str, str] = {}
    for name in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"):
        el = soup.find("input", {"name": name})
        if el is None:
            raise ParentPayParseError(f"Missing WebForms hidden field: {name}")
        value = el.get("value")
        fields[name] = str(value) if value is not None else ""
    return WebFormsState(
        viewstate=fields["__VIEWSTATE"],
        viewstategenerator=fields["__VIEWSTATEGENERATOR"],
        eventvalidation=fields["__EVENTVALIDATION"],
    )
