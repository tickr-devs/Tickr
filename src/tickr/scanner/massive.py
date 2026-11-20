"""Massive reads for the shadow scanner: the WebSocket minute-aggregate stream (`AM.*`, every
ticker) and two REST reference reads (eligible tickers, the day's splits).

The API key is passed in by name-resolved value from the caller and only ever sent to Massive
(WebSocket auth message, REST `Authorization` header); it is never put in a URL, a record, a log
line or an exception text. Errors carry the exception type, an HTTP status or Massive's status
word only.

`websockets` (optional extra `massive`) is imported when a stream is opened, so everything else
in the scanner, and its tests, run without it.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from tickr.scanner.lists import Split

# Massive WebSocket protocol constants (same protocol as Polygon.io).
SUBSCRIPTION = "AM.*"  # minute aggregates of every ticker
EVENT_KEY = "ev"
STATUS_EVENT = "status"
STATUS_KEY = "status"
AGGREGATE_EVENT = "AM"
CONNECTED = "connected"
AUTH_SUCCESS = "auth_success"
SUBSCRIBED = "success"
TICKERS_PATH = "/v3/reference/tickers"
SPLITS_PATH = "/v3/reference/splits"
HTTP_OK = 200


class FeedError(Exception):
    """The stream or a reference read failed; the scanner records the cause and tries again."""


class FeedRefused(FeedError):
    """Massive refused the session (authentication, too many connections, plan)."""


class Feed(Protocol):
    def open(self) -> None: ...

    def receive(self, timeout_seconds: float) -> list[dict[str, Any]]: ...

    def close(self) -> None: ...


def parse_events(text: str | bytes) -> list[dict[str, Any]]:
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise FeedError("message is not JSON") from exc
    events = raw if isinstance(raw, list) else [raw]
    if not all(isinstance(e, dict) for e in events):
        raise FeedError("message is not a list of events")
    return events


def _statuses(events: list[dict[str, Any]]) -> list[str]:
    return [str(e.get(STATUS_KEY)) for e in events if e.get(EVENT_KEY) == STATUS_EVENT]


@dataclass
class MassiveStream:
    """One WebSocket session: connect, authenticate, subscribe to `AM.*`."""

    url: str
    api_key: str
    open_timeout_seconds: float
    max_message_bytes: int
    _socket: Any = None

    def _expect(self, wanted: str, timeout: float) -> None:
        events = parse_events(self._socket.recv(timeout=timeout))
        found = _statuses(events)
        if wanted not in found:
            raise FeedRefused(f"expected {wanted}, got {','.join(found) or 'no status'}")

    def open(self) -> None:
        from websockets.exceptions import WebSocketException
        from websockets.sync.client import connect

        try:
            self._socket = connect(self.url, open_timeout=self.open_timeout_seconds,
                                   max_size=self.max_message_bytes)
            self._expect(CONNECTED, self.open_timeout_seconds)
            self._socket.send(json.dumps({"action": "auth", "params": self.api_key}))
            self._expect(AUTH_SUCCESS, self.open_timeout_seconds)
            self._socket.send(json.dumps({"action": "subscribe", "params": SUBSCRIPTION}))
            self._expect(SUBSCRIBED, self.open_timeout_seconds)
        except FeedError:
            self.close()
            raise
        except (OSError, TimeoutError, WebSocketException) as exc:
            self.close()
            raise FeedError(type(exc).__name__) from None

    def receive(self, timeout_seconds: float) -> list[dict[str, Any]]:
        """The events of the next message, or [] when none came within the timeout."""
        from websockets.exceptions import WebSocketException

        try:
            return parse_events(self._socket.recv(timeout=timeout_seconds))
        except TimeoutError:
            return []
        except (OSError, WebSocketException) as exc:
            raise FeedError(type(exc).__name__) from None

    def close(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            except Exception:  # noqa: BLE001 - closing a broken socket must not raise
                pass
            self._socket = None


Opener = Callable[[urllib.request.Request, float], Any]


def _urlopen(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - https from config


@dataclass
class MassiveReference:
    """Paged REST reads. `next_url` pages are followed only on the configured host."""

    base_url: str
    api_key: str
    timeout_seconds: float
    page_limit: int
    max_pages: int
    opener: Opener = _urlopen

    def _get(self, url: str) -> dict[str, Any]:
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.api_key}"})
        try:
            with self.opener(request, self.timeout_seconds) as response:
                if response.status != HTTP_OK:
                    raise FeedError(f"HTTP {response.status}")
                body = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise FeedError(f"HTTP {exc.code}") from None
        except (OSError, ValueError) as exc:
            raise FeedError(type(exc).__name__) from None
        if not isinstance(body, dict) or not isinstance(body.get("results", []), list):
            raise FeedError("reply without a results list")
        return body

    def _pages(self, path: str, query: dict[str, str]) -> list[dict[str, Any]]:
        host = urllib.parse.urlsplit(self.base_url).netloc
        url: str | None = f"{self.base_url}{path}?{urllib.parse.urlencode(query)}"
        rows: list[dict[str, Any]] = []
        for _ in range(self.max_pages):
            if url is None:
                return rows
            if urllib.parse.urlsplit(url).netloc != host:
                raise FeedError("next_url on another host")
            body = self._get(url)
            rows.extend(body.get("results", []))
            url = body.get("next_url")
        if url is not None:
            raise FeedError(f"more than {self.max_pages} pages")
        return rows

    def eligible_tickers(self, types: tuple[str, ...]) -> dict[str, str]:
        """Active stock tickers of each type: ticker -> type."""
        found: dict[str, str] = {}
        for kind in types:
            rows = self._pages(TICKERS_PATH, {"market": "stocks", "type": kind, "active": "true",
                                              "limit": str(self.page_limit)})
            for row in rows:
                ticker = row.get("ticker")
                if not isinstance(ticker, str) or not ticker:
                    raise FeedError("ticker row without a ticker")
                found[ticker] = kind
        if not found:
            raise FeedError("no eligible tickers")
        return found

    def splits(self, day: date) -> list[Split]:
        rows = self._pages(SPLITS_PATH, {"execution_date": day.isoformat(),
                                         "limit": str(self.page_limit)})
        out = []
        for row in rows:
            try:
                out.append(Split(str(row["ticker"]), float(row["split_from"]),
                                 float(row["split_to"])))
            except (KeyError, TypeError, ValueError):
                raise FeedError("split row without ticker, split_from or split_to") from None
        return out
