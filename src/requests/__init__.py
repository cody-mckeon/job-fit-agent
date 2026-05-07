"""Minimal requests-compatible shim for offline environments."""

from __future__ import annotations

from urllib.error import HTTPError, URLError
from urllib.request import urlopen


class RequestException(Exception):
    """Base request exception."""


class Timeout(RequestException):
    """Timeout exception."""


class HTTPStatusError(RequestException):
    """HTTP status exception."""


class Response:
    def __init__(self, payload: bytes, status_code: int):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise HTTPStatusError(f"HTTP {self.status_code}")

    def json(self):
        import json

        return json.loads(self._payload.decode("utf-8"))


def get(url: str, timeout: int = 10) -> Response:
    try:
        with urlopen(url, timeout=timeout) as response:
            return Response(response.read(), response.status)
    except HTTPError as exc:
        raise HTTPStatusError(str(exc)) from exc
    except URLError as exc:
        raise RequestException(str(exc)) from exc
