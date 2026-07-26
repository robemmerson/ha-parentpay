"""Minimal aiohttp request mock for the client tests.

Replaces `aioresponses`, which builds `aiohttp.ClientResponse` objects itself and
broke when aiohttp 3.14 made `stream_writer` a required keyword-only argument
(pnuckowski/aioresponses#289, open with no release). Home Assistant pins
`aiohttp==3.14.3` exactly, so there was no version pair that satisfied both.

This patches `ClientSession._request` instead — the single seam every
`.get()` / `.post()` / `.request()` call funnels through — and returns a stub
response. aiohttp's own `_RequestContextManager` still wraps it, so `async with`
semantics are unchanged and nothing depends on aiohttp response internals.

Responses are queued per (method, url) and consumed in registration order, so a
url registered twice serves its two bodies in turn.
"""

from __future__ import annotations

import json as _json
from collections import defaultdict, deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import aiohttp
from yarl import URL


class MockResponse:
    """Stand-in for `aiohttp.ClientResponse` covering what the client uses."""

    def __init__(self, *, status: int, body: str) -> None:
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body

    async def json(self, *, content_type: str | None = None) -> Any:
        return _json.loads(self._body)

    def release(self) -> None:
        """No-op; nothing to drain."""

    def close(self) -> None:
        """No-op."""

    async def __aenter__(self) -> MockResponse:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self.release()


@dataclass
class RecordedRequest:
    """A request the client made while the mock was installed."""

    method: str
    url: str
    kwargs: dict[str, Any] = field(default_factory=dict)


class MockHTTP:
    """Registry of queued responses plus a log of what was requested."""

    def __init__(self) -> None:
        self._queues: dict[tuple[str, str], deque[MockResponse]] = defaultdict(deque)
        self.requests: list[RecordedRequest] = []

    def get(
        self,
        url: str,
        *,
        status: int = 200,
        body: str = "",
        payload: Any = None,
    ) -> None:
        self._register("GET", url, status=status, body=body, payload=payload)

    def post(
        self,
        url: str,
        *,
        status: int = 200,
        body: str = "",
        payload: Any = None,
    ) -> None:
        self._register("POST", url, status=status, body=body, payload=payload)

    def _register(
        self,
        method: str,
        url: str,
        *,
        status: int,
        body: str,
        payload: Any,
    ) -> None:
        if payload is not None:
            body = _json.dumps(payload)
        self._queues[method, str(URL(url))].append(
            MockResponse(status=status, body=body)
        )

    def _consume(self, method: str, url: str) -> MockResponse:
        key = (method.upper(), str(URL(url)))
        queue = self._queues.get(key)
        if not queue:
            raise AssertionError(f"no mocked response left for {method} {url}")
        return queue.popleft()

    def last_request(self, method: str, url: str) -> RecordedRequest:
        """Most recent recorded request for a method/url, for asserting on bodies."""
        target = str(URL(url))
        for recorded in reversed(self.requests):
            if recorded.method == method.upper() and recorded.url == target:
                return recorded
        raise AssertionError(f"expected at least one {method} against {url}")


@contextmanager
def mock_http() -> Iterator[MockHTTP]:
    """Install the mock for the duration of the block."""
    mock = MockHTTP()
    original = aiohttp.ClientSession._request

    async def _fake_request(
        self: aiohttp.ClientSession,
        method: str,
        url: str | URL,
        **kwargs: Any,
    ) -> MockResponse:
        mock.requests.append(
            RecordedRequest(method=method.upper(), url=str(URL(str(url))), kwargs=kwargs)
        )
        return mock._consume(method, str(url))

    # Patching a private aiohttp method is deliberate: it is the one seam all
    # request helpers share, and it keeps us clear of ClientResponse's signature.
    aiohttp.ClientSession._request = _fake_request  # type: ignore[method-assign]
    try:
        yield mock
    finally:
        aiohttp.ClientSession._request = original  # type: ignore[method-assign]
