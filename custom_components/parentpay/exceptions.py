"""Exceptions raised by the ParentPay client and parsers."""
from __future__ import annotations


class ParentPayError(Exception):
    """Base exception."""


class ParentPayAuthError(ParentPayError):
    """Raised when login fails or a session has expired."""


class ParentPayParseError(ParentPayError):
    """Raised when an expected element is missing from a response."""

    def __init__(self, message: str, snippet: str | None = None) -> None:
        super().__init__(message)
        self.snippet = snippet


class ParentPayHTTPError(ParentPayError):
    """Raised on unexpected HTTP status codes."""
