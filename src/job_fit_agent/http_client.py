"""HTTP client compatibility helpers.

This module intentionally delegates to the third-party ``requests`` package.
It exists as the home for any app-local HTTP helper imports so the repository
never provides a top-level ``requests`` package that can shadow the dependency.
"""

from __future__ import annotations

from requests import HTTPError, RequestException, Response, Timeout, get

__all__ = ["HTTPError", "RequestException", "Response", "Timeout", "get"]
