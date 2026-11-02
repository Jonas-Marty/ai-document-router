"""Logging setup, and the context that ties a log line to the request or tick it came from.

Without a call to `configure_logging` nothing below WARNING reaches the console at all:
Python's last-resort handler only prints warnings and up, so every `logger.info` in the
poller, the router and the AI modules would be silently dropped.

Stdlib only. A request id and a JSON line are a few dozen lines here, and not worth a
logging framework as a runtime dependency.
"""

import json
import logging
import logging.config
import re
import time
from collections.abc import Awaitable, Callable, Iterator, MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.services import metrics

# "req=3f9c1a2b7d4e" inside a request, "tick=…" inside a poller run, "" otherwise. One
# variable rather than one per kind: a line only ever belongs to one of them.
context_var: ContextVar[str] = ContextVar("log_context", default="")

REQUEST_ID_HEADER = "X-Request-ID"
# An incoming id is echoed into logs and a response header, so anything that could smuggle
# a newline or a second header value is replaced rather than trusted.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")

# Paths polled on an interval. Logged at DEBUG so a healthy instance's INFO log is about
# documents, not about the container healthcheck asking every 30 seconds.
_QUIET_PATHS = ("/api/v1/health", "/api/v1/metrics")

TEXT_FORMAT = "%(asctime)s %(levelname)-7s %(name)s [%(context)s] %(message)s"

request_logger = logging.getLogger("app.request")


def new_id() -> str:
    return uuid4().hex[:12]


def request_id() -> str | None:
    """The current request's id, for messages that ask the person to quote it."""
    value = context_var.get()
    return value.removeprefix("req=") if value.startswith("req=") else None


@contextmanager
def bound(context: str) -> Iterator[None]:
    """Tag every log line inside the block, e.g. `with bound(f"tick={new_id()}")`."""
    token = context_var.set(context)
    try:
        yield
    finally:
        context_var.reset(token)


class ContextFilter(logging.Filter):
    """Attached to the handler, not a logger, so it tags records from every library too."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.context = context_var.get() or "-"
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line, for a log collector that parses rather than greps."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        context = getattr(record, "context", "-")
        if context != "-":
            entry["context"] = context
        if record.exc_info:
            entry["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(level: str, fmt: str) -> None:
    formatter: dict[str, object] = (
        {"()": JsonFormatter} if fmt == "json" else {"format": TEXT_FORMAT}
    )
    logging.config.dictConfig(
        {
            "version": 1,
            # Loggers are created at import, long before this runs; disabling them would
            # silence exactly the modules this exists to hear from.
            "disable_existing_loggers": False,
            "filters": {"context": {"()": ContextFilter}},
            "formatters": {"default": formatter},
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stderr",
                    "formatter": "default",
                    "filters": ["context"],
                }
            },
            "root": {"level": level, "handlers": ["console"]},
            "loggers": {
                # uvicorn installs its own handlers before importing the app. Emptied and
                # propagated so its lines share the format and the context column.
                "uvicorn": {"handlers": [], "propagate": True},
                "uvicorn.error": {"handlers": [], "propagate": True},
                # Replaced by RequestLogMiddleware, which knows the request id and duration.
                "uvicorn.access": {"handlers": [], "propagate": False},
                # httpx logs every request URL at INFO; that is one line per WebDAV PROPFIND
                # and per model call, and our own lines already say what matters about them.
                "httpx": {"level": "WARNING"},
                "httpcore": {"level": "WARNING"},
                "apscheduler": {"level": "WARNING"},
                "tzlocal": {"level": "WARNING"},
                "webdav4": {"level": "WARNING"},
            },
        }
    )


Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class RequestLogMiddleware:
    """Give each request an id, log one line when it finishes, and echo the id back.

    Plain ASGI rather than BaseHTTPMiddleware: document content is a StreamingResponse, and
    this needs to see the status as it is sent without buffering or re-wrapping the body.
    The context variable is deliberately left set when the app raises, so the exception
    handler in main.py can quote the same id it was logged under.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = _header(scope, REQUEST_ID_HEADER)
        rid = incoming if incoming and _VALID_REQUEST_ID.match(incoming) else new_id()
        context_var.set(f"req={rid}")

        started = time.perf_counter()
        status = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER.lower().encode(), rid.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            path = scope.get("path", "")
            _observe(scope, status, duration_ms / 1000)
            level = logging.DEBUG if path in _QUIET_PATHS and status < 400 else logging.INFO
            request_logger.log(
                level, "%s %s %d %.0fms", scope.get("method", ""), path, status, duration_ms
            )


def _observe(scope: Scope, status: int, seconds: float) -> None:
    """HTTP metrics, labelled by the route template the router matched.

    The router writes the matched route into the same scope dict on its way in, so it is
    there by the time the response has gone out. Its path is the template as the route
    declared it, without the `/api/v1` every router is mounted under -- which is fine, since
    nothing is mounted anywhere else. The raw path would put every document id in a label;
    anything no route matched (a scanner probing for /wp-admin) shares one.
    """
    route = getattr(scope.get("route"), "path", None) or "unmatched"
    method = scope.get("method", "")
    metrics.http_requests.labels(method, route, str(status)).inc()
    metrics.http_request_duration.labels(method, route).observe(seconds)


def _header(scope: Scope, name: str) -> str | None:
    wanted = name.lower().encode()
    for key, value in scope.get("headers", []):
        if key == wanted:
            decoded: str = value.decode("latin-1")
            return decoded
    return None
