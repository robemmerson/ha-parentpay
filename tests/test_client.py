"""Tests for ParentPayClient."""
from __future__ import annotations

import json
import pathlib
from datetime import date

import aiohttp
import pytest
from aioresponses import aioresponses
from yarl import URL

from custom_components.parentpay.client import ParentPayClient
from custom_components.parentpay.const import (
    ARCHIVE_URL,
    HOME_URL,
    LOGIN_URL,
    PAYMENT_ITEMS_URL,
)
from custom_components.parentpay.exceptions import ParentPayAuthError

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _load_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _load_json(name: str) -> dict:
    return json.loads(_load_text(name))


@pytest.fixture
async def http_session() -> aiohttp.ClientSession:
    async with aiohttp.ClientSession() as s:
        yield s


async def test_login_sends_credentials_and_detects_success(
    http_session: aiohttp.ClientSession,
) -> None:
    client = ParentPayClient(http_session, username="u@example.com", password="pw")
    with aioresponses() as m:
        m.post(LOGIN_URL, status=200, payload=_load_json("login_success.json"))
        await client.login()
    assert client.is_logged_in is True


async def test_login_raises_auth_error_on_bad_credentials(
    http_session: aiohttp.ClientSession,
) -> None:
    client = ParentPayClient(http_session, username="u@example.com", password="bad")
    with aioresponses() as m:
        m.post(
            LOGIN_URL,
            status=400,
            payload={"message": "Authentication failed."},
        )
        with pytest.raises(ParentPayAuthError, match="Authentication failed"):
            await client.login()
    assert client.is_logged_in is False


async def test_login_raises_auth_error_if_account_not_activated(
    http_session: aiohttp.ClientSession,
) -> None:
    client = ParentPayClient(http_session, username="u@example.com", password="pw")
    with aioresponses() as m:
        m.post(LOGIN_URL, status=200, payload={"isActivated": False})
        with pytest.raises(ParentPayAuthError):
            await client.login()
    assert client.is_logged_in is False


async def test_fetch_retries_once_after_session_expiry(
    http_session: aiohttp.ClientSession,
) -> None:
    client = ParentPayClient(http_session, username="u@example.com", password="pw")
    with aioresponses() as m:
        m.post(LOGIN_URL, status=200, payload=_load_json("login_success.json"))
        await client.login()
        # First home-page fetch: session expired, server returns login page HTML
        m.get(HOME_URL, status=200, body="<html><body>Please log in</body></html>")
        # Re-login succeeds
        m.post(LOGIN_URL, status=200, payload=_load_json("login_success.json"))
        # Retry returns real balances HTML
        m.get(HOME_URL, status=200, body=_load_text("balances.html"))
        body = await client._authed_get(HOME_URL)
    assert "data-consumer-data" in body


async def test_fetch_raises_auth_error_if_retry_also_fails(
    http_session: aiohttp.ClientSession,
) -> None:
    client = ParentPayClient(http_session, username="u@example.com", password="pw")
    with aioresponses() as m:
        m.post(LOGIN_URL, status=200, payload=_load_json("login_success.json"))
        await client.login()
        m.get(HOME_URL, status=200, body="<html><body>Please log in</body></html>")
        m.post(LOGIN_URL, status=400, payload={"message": "Authentication failed."})
        with pytest.raises(ParentPayAuthError):
            await client._authed_get(HOME_URL)


async def test_fetch_home_returns_balances_and_payments(
    http_session: aiohttp.ClientSession,
) -> None:
    client = ParentPayClient(http_session, username="u@example.com", password="pw")
    with aioresponses() as m:
        m.post(LOGIN_URL, status=200, payload=_load_json("login_success.json"))
        m.get(HOME_URL, status=200, body=_load_text("balances.html"))
        snapshot = await client.fetch_home()
    assert {b.child_id for b in snapshot.balances} == {"11111111", "22222222"}
    assert isinstance(snapshot.recent_payments, list)


async def test_fetch_payment_items_returns_parsed_items(
    http_session: aiohttp.ClientSession,
) -> None:
    client = ParentPayClient(http_session, username="u@example.com", password="pw")
    with aioresponses() as m:
        m.post(LOGIN_URL, status=200, payload=_load_json("login_success.json"))
        m.get(PAYMENT_ITEMS_URL, status=200, body=_load_text("payment_items.html"))
        items = await client.fetch_payment_items()
    assert len(items) > 0
    assert all(it.payment_item_id for it in items)


async def test_fetch_archive_returns_recent_rows(
    http_session: aiohttp.ClientSession,
) -> None:
    client = ParentPayClient(http_session, username="u@example.com", password="pw")
    with aioresponses() as m:
        m.post(LOGIN_URL, status=200, payload=_load_json("login_success.json"))
        m.get(ARCHIVE_URL, status=200, body=_load_text("archive_initial.html"))
        rows = await client.fetch_archive()
    assert len(rows) > 0


def _archive_post_body(m: aioresponses) -> dict[str, str]:
    """Pull the form body out of the most recent POST against ARCHIVE_URL."""
    calls = m.requests.get(("POST", URL(ARCHIVE_URL))) or []
    assert calls, "expected at least one POST against the archive URL"
    data = calls[-1].kwargs.get("data")
    assert isinstance(data, dict), f"expected dict body, got {type(data)!r}"
    return data


async def test_fetch_archive_range_does_get_then_post(
    http_session: aiohttp.ClientSession,
) -> None:
    client = ParentPayClient(http_session, username="u@example.com", password="pw")
    with aioresponses() as m:
        m.post(LOGIN_URL, status=200, payload=_load_json("login_success.json"))
        m.get(ARCHIVE_URL, status=200, body=_load_text("archive_initial.html"))
        m.post(ARCHIVE_URL, status=200, body=_load_text("archive_sample.html"))
        rows = await client.fetch_archive_range(
            date(2025, 4, 19), date(2026, 4, 19)
        )
        body = _archive_post_body(m)
    assert body["__EVENTTARGET"] == "ctl00$cmdSearch"
    assert body["__EVENTARGUMENT"] == ""
    assert body["__VIEWSTATE"] == "TESTVIEWSTATE_INITIAL"
    assert body["__VIEWSTATEGENERATOR"] == "TESTGEN_INITIAL"
    assert body["__EVENTVALIDATION"] == "TESTEV_INITIAL"
    assert body["ctl00$selChoosePupil"] == "0"
    assert body["ctl00$selChooseService"] == "0"
    assert body["ctl00$txtChooseStartDate"] == "19/04/2025"
    assert body["ctl00$txtChooseEndDate"] == "19/04/2026"
    assert len(rows) > 900


async def test_fetch_archive_range_uses_ddmm_date_format(
    http_session: aiohttp.ClientSession,
) -> None:
    client = ParentPayClient(http_session, username="u@example.com", password="pw")
    with aioresponses() as m:
        m.post(LOGIN_URL, status=200, payload=_load_json("login_success.json"))
        m.get(ARCHIVE_URL, status=200, body=_load_text("archive_initial.html"))
        m.post(ARCHIVE_URL, status=200, body=_load_text("archive_sample.html"))
        await client.fetch_archive_range(date(2026, 1, 5), date(2026, 12, 31))
        body = _archive_post_body(m)
    # DD/MM/YYYY — single-digit days/months are zero-padded
    assert body["ctl00$txtChooseStartDate"] == "05/01/2026"
    assert body["ctl00$txtChooseEndDate"] == "31/12/2026"


async def test_fetch_archive_range_returns_parsed_rows(
    http_session: aiohttp.ClientSession,
) -> None:
    client = ParentPayClient(http_session, username="u@example.com", password="pw")
    with aioresponses() as m:
        m.post(LOGIN_URL, status=200, payload=_load_json("login_success.json"))
        m.get(ARCHIVE_URL, status=200, body=_load_text("archive_initial.html"))
        m.post(ARCHIVE_URL, status=200, body=_load_text("archive_sample.html"))
        rows = await client.fetch_archive_range(
            date(2025, 4, 19), date(2026, 4, 19)
        )
    assert {r.child_id for r in rows} == {"11111111", "22222222"}
    assert {r.payment_method for r in rows} >= {"Meal", "Parent Account"}
