"""HTTP client for ParentPay."""
from __future__ import annotations

import logging

import aiohttp

from .const import (
    ARCHIVE_URL,
    DEFAULT_USER_AGENT,
    HOME_URL,
    LOGIN_FIELD_PASSWORD,
    LOGIN_FIELD_USER,
    LOGIN_URL,
    PAYMENT_ITEMS_URL,
)
from .exceptions import ParentPayAuthError
from .models import ArchiveRow, HomeSnapshot, PaymentItem
from .parsers import (
    parse_archive,
    parse_home_balances,
    parse_home_recent_meals,
    parse_home_recent_payments,
    parse_login_response,
    parse_payment_items,
)

_LOGGER = logging.getLogger(__name__)

_AUTHENTICATED_MARKER = "data-consumer-data"


class ParentPayClient:
    """Async client that encapsulates auth + scraping."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        username: str,
        password: str,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._logged_in = False

    @property
    def is_logged_in(self) -> bool:
        return self._logged_in

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": DEFAULT_USER_AGENT}

    async def login(self) -> None:
        payload = {
            LOGIN_FIELD_USER: self._username,
            LOGIN_FIELD_PASSWORD: self._password,
        }
        _LOGGER.debug("POST %s (login)", LOGIN_URL)
        async with self._session.post(
            LOGIN_URL,
            data=payload,
            headers=self._headers(),
            allow_redirects=True,
        ) as resp:
            status = resp.status
            try:
                body = await resp.json(content_type=None)
            except aiohttp.ContentTypeError:
                body = {}
        ok, reason = parse_login_response(status=status, payload=body)
        if not ok:
            self._logged_in = False
            raise ParentPayAuthError(reason or "Login failed")
        self._logged_in = True

    async def _authed_get(self, url: str) -> str:
        return await self._authed_request("GET", url)

    async def _authed_post(self, url: str, *, data: dict[str, str]) -> str:
        return await self._authed_request("POST", url, data=data)

    async def _authed_request(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, str] | None = None,
    ) -> str:
        """Perform a request, re-logging-in once if the response looks like the login page."""
        if not self._logged_in:
            await self.login()
        body = await self._raw_request(method, url, data=data)
        if not self._looks_authenticated(body):
            _LOGGER.debug("Session appears expired, re-logging in and retrying %s", url)
            self._logged_in = False
            await self.login()
            body = await self._raw_request(method, url, data=data)
            if not self._looks_authenticated(body):
                raise ParentPayAuthError("Session retry failed")
        return body

    async def _raw_request(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, str] | None = None,
    ) -> str:
        async with self._session.request(
            method,
            url,
            data=data,
            headers=self._headers(),
            allow_redirects=True,
        ) as resp:
            return await resp.text()

    @staticmethod
    def _looks_authenticated(body: str) -> bool:
        return _AUTHENTICATED_MARKER in body

    async def fetch_home(self) -> HomeSnapshot:
        """One-shot fetch: balances + recent meals + recent parent-account payments."""
        body = await self._authed_get(HOME_URL)
        return HomeSnapshot(
            balances=parse_home_balances(body),
            recent_meals=parse_home_recent_meals(body),
            recent_payments=parse_home_recent_payments(body),
        )

    async def fetch_payment_items(self) -> list[PaymentItem]:
        body = await self._authed_get(PAYMENT_ITEMS_URL)
        return parse_payment_items(body)

    async def fetch_archive(self) -> list[ArchiveRow]:
        """Fetch the recent archive rows.

        v1 uses the default GET (no date range), which returns ~the last 8 rows.
        A future v2 can add an `fetch_archive(start, end)` POST path using the
        ASP.NET WebForms calendar postback.
        """
        body = await self._authed_get(ARCHIVE_URL)
        return parse_archive(body)
