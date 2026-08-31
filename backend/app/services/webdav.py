"""The only module that talks to WebDAV. Nothing else imports webdav4.

Two rules from CLAUDE.md are structural here rather than conventional:

1. There is no delete. `Client.remove` is deliberately never wrapped.
2. There is no overwrite. `move()` always sends `Overwrite: F`; a destination that already
   exists surfaces as a conflict instead of clobbering the file. `replace()` is the single,
   narrow exception -- see its docstring.

Every path crossing this boundary is normalized and checked against the permitted roots,
because `..` handed to webdav4 escapes the base URL's own path prefix (see paths.py).
"""

import io
import logging
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from webdav4.client import (
    BadGatewayError,
    Client,
    ClientError,
    ForbiddenOperation,
    InsufficientStorage,
    ResourceAlreadyExists,
    ResourceConflict,
    ResourceLocked,
    ResourceNotFound,
)

from app.config import settings as config
from app.services.errors import (
    AppError,
    NotFoundError,
    OutsideAllowedRootsError,
    WebDAVConflict,
    WebDAVUnreachable,
)
from app.services.paths import assert_within_allowed_roots, is_within, normalize_path
from app.services.times import to_utc_aware

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 30.0

# Module scope, not instance scope: a WebDavService is built per request (it has to see the
# current allowed_root_folders), so an instance cache would never hit. The poller in M4 runs
# in the same process and FastAPI runs sync handlers in a threadpool, so this is shared
# across threads and needs the lock.
_cache_lock = threading.Lock()
_listing_cache: dict[str, tuple[float, list["WebDavEntry"]]] = {}

_probe_client_lock = threading.Lock()
_probe_client_instance: "Client | None" = None


class WebDAVError(AppError):
    """A WebDAV operation failed for a reason the server was reachable enough to report."""

    code = "webdav_error"
    status_code = 502


@dataclass(frozen=True)
class WebDavEntry:
    """One directory entry. Deliberately our own shape, so webdav4's dict never leaks out."""

    path: str
    name: str
    is_dir: bool
    size_bytes: int | None = None
    modified: datetime | None = None
    content_type: str | None = None
    etag: str | None = None


def parent_of(path: str) -> str:
    """The containing directory of a normalized absolute path. Parent of '/' is '/'."""
    normalized = normalize_path(path)
    if normalized == "/":
        return "/"
    parent = normalized.rsplit("/", 1)[0]
    return parent or "/"


def build_client(timeout: float | None = None) -> Client:
    """Construct a webdav4 client from environment configuration."""
    return Client(
        base_url=config.webdav_base_url,
        auth=(config.webdav_username, config.webdav_password),
        timeout=config.webdav_timeout_seconds if timeout is None else timeout,
    )


def probe_reachable() -> bool:
    """Cheap liveness check for /health. Never raises and never blocks for long.

    Uses its own short timeout rather than the operation timeout, because the frontend
    polls /health on an interval and a slow probe delays the outage banner.
    """
    if not config.webdav_base_url:
        return False

    try:
        watch_folder = normalize_path(config.webdav_watch_folder).lstrip("/")
    except ValueError:
        # A malformed WEBDAV_WATCH_FOLDER is a configuration mistake, not an outage.
        # Logged at warning so it is distinguishable from a server that is simply down.
        logger.warning("WEBDAV_WATCH_FOLDER is not a valid path: %r", config.webdav_watch_folder)
        return False

    try:
        _probe_client().exists(watch_folder)
    except Exception as exc:  # noqa: BLE001 - a health probe must never propagate
        logger.debug("WebDAV health probe failed: %s", exc)
        return False
    return True


def _probe_client() -> Client:
    """One reused client for health probes.

    /health is polled by both the frontend and the container healthcheck, so building a
    fresh connection pool each time would mean a TCP and TLS handshake every few seconds
    with no connection reuse.
    """
    global _probe_client_instance
    with _probe_client_lock:
        if _probe_client_instance is None:
            _probe_client_instance = build_client(timeout=config.webdav_health_timeout_seconds)
        return _probe_client_instance


def reset_probe_client() -> None:
    """Drop the cached probe client. Used by tests."""
    global _probe_client_instance
    with _probe_client_lock:
        _probe_client_instance = None


def clear_cache() -> None:
    """Drop every cached listing. Used by tests and after a configuration change."""
    with _cache_lock:
        _listing_cache.clear()


def invalidate(path: str) -> None:
    """Invalidate the cached listing for one directory."""
    with _cache_lock:
        _listing_cache.pop(normalize_path(path), None)


class WebDavService:
    """WebDAV operations, with every path checked against the permitted roots.

    `permitted_roots` is the app's whole universe: the allowed roots plus the deliberate
    exceptions (trash folder, watch folder). It is the backstop that keeps a bad path from
    reaching the server at all. Callers acting on user input additionally enforce the
    stricter SPEC 7.2 rule (inside an *allowed root*) via assert_within_allowed_roots.
    """

    def __init__(
        self,
        client: Client,
        permitted_roots: Iterable[str],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._permitted_roots = [normalize_path(root) for root in permitted_roots]
        self._clock = clock

    # -- reads ----------------------------------------------------------------------

    def list_dir(self, path: str) -> list[WebDavEntry]:
        """List a directory. Cached for CACHE_TTL_SECONDS, invalidated on writes."""
        normalized = self._check(path)

        cached = self._cache_get(normalized)
        if cached is not None:
            return cached

        with _translate_errors():
            raw = self._client.ls(self._wire(normalized), detail=True)

        entries = [
            entry
            for entry in (self._to_entry(item, exclude=normalized) for item in raw)
            if entry is not None
        ]
        self._cache_put(normalized, entries)
        return entries

    def list_dirs_only(self, path: str) -> list[WebDavEntry]:
        """Subdirectories of a directory. Filters the same cached listing as list_dir."""
        return [entry for entry in self.list_dir(path) if entry.is_dir]

    def exists(self, path: str) -> bool:
        """Whether a path exists. Never cached.

        The pre-move collision check in M5 depends on this being a live read; a stale
        'no collision' answer would let approve overwrite a file.
        """
        normalized = self._check(path)
        with _translate_errors():
            return bool(self._client.exists(self._wire(normalized)))

    def stat(self, path: str) -> WebDavEntry:
        """Metadata for a single path. Never cached, for the same reason as exists()."""
        normalized = self._check(path)
        with _translate_errors():
            info = self._client.info(self._wire(normalized))
        entry = self._to_entry(info)
        if entry is None:
            raise WebDAVError(f"Could not read metadata for '{normalized}'.")
        return entry

    def read_stream(self, path: str, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
        """Stream a file's bytes.

        Returns a generator that owns the underlying context manager, so the caller gets an
        open stream rather than one closed on the way out.
        """
        normalized = self._check(path)
        wire = self._wire(normalized)
        client = self._client

        def _chunks() -> Iterator[bytes]:
            with _translate_errors(), client.open(wire, mode="rb") as handle:
                while True:
                    chunk = handle.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk

        return _chunks()

    # -- writes ---------------------------------------------------------------------

    def move(self, from_path: str, to_path: str) -> None:
        """Move a file. Never overwrites -- a existing destination raises WebDAVConflict.

        There is deliberately no `overwrite` parameter: CLAUDE.md rule 2 makes clobbering
        unavailable rather than merely discouraged.
        """
        source = self._check(from_path)
        destination = self._check(to_path)

        with _translate_errors():
            self._client.move(self._wire(source), self._wire(destination), overwrite=False)

        # A move changes the *contents* of both containing directories, not the paths moved.
        invalidate(parent_of(source))
        invalidate(parent_of(destination))

    def replace(self, path: str, data: bytes, content_type: str | None = None) -> None:
        """Write bytes over a file that must already be there.

        The only method in this module that sends content, and shaped so it cannot quietly
        become a general-purpose upload: the destination has to exist already, so this can
        replace a file but never create one somewhere unexpected, and there is no variant
        that takes a folder and a name.

        Its one caller is approve's OCR write-back, replacing the file the MOVE put at this
        path moments earlier with a searchable copy of that same document. CLAUDE.md rule 2
        is about a move clobbering a file someone else put somewhere; this overwrites our
        own, on purpose, with the same document plus a text layer. Rule 1 is untouched --
        nothing here deletes, and a failure leaves the original exactly where it was filed.
        """
        normalized = self._check(path)
        # Live, uncached, and for the same reason approve re-checks before its move: acting
        # on a stale "yes, that exists" is how you write over the wrong thing.
        if not self.exists(normalized):
            raise NotFoundError(f"There is no file at '{normalized}' to replace.")

        headers = {"Content-Type": content_type} if content_type else None
        with _translate_errors():
            self._client.upload_fileobj(
                io.BytesIO(data),
                self._wire(normalized),
                overwrite=True,
                size=len(data),
                headers=headers,
            )

        # The file's size and etag changed, so the parent's cached listing is now wrong.
        invalidate(parent_of(normalized))

    def mkdir(self, path: str) -> None:
        """Create a single directory. Its parent must already exist."""
        normalized = self._check(path)
        with _translate_errors():
            self._client.mkdir(self._wire(normalized))
        invalidate(parent_of(normalized))

    def mkdir_p(self, path: str) -> None:
        """Create a directory and any missing parents. No-op if it already exists.

        Descends from the permitted root that contains the target rather than from '/'.
        Walking up from the filesystem root would visit directories above the root -- for a
        nested configuration like allowed=/Documents/Filed, that means '/Documents', which
        is outside the permitted set and would refuse its own legitimate operation.
        """
        target = self._check(path)
        root = self._containing_root(target)

        remainder = target[len(root) :] if root != "/" else target
        current = root
        pending = [root]
        for segment in (part for part in remainder.split("/") if part):
            # Rebuild through normalize_path so a separator smuggled into a single segment
            # cannot widen the path we are about to create.
            current = normalize_path(f"{current}/{segment}")
            pending.append(current)

        for directory in pending:
            if self.exists(directory):
                continue
            try:
                with _translate_errors():
                    self._client.mkdir(self._wire(directory))
            except WebDAVConflict:
                # Raced with someone else creating it; that is the state we wanted anyway.
                continue
            invalidate(parent_of(directory))

    def _containing_root(self, normalized: str) -> str:
        """The most specific permitted root containing `normalized`.

        Most specific, not first: the watch folder may legitimately sit inside an allowed
        root, and starting from the deeper one creates the fewest directories.
        """
        matches = [root for root in self._permitted_roots if is_within(root, normalized)]
        if not matches:
            raise OutsideAllowedRootsError(f"'{normalized}' is outside the allowed folders.")
        return max(matches, key=len)

    # -- internals ------------------------------------------------------------------

    def _check(self, path: str) -> str:
        return assert_within_allowed_roots(path, self._permitted_roots)

    @staticmethod
    def _wire(normalized: str) -> str:
        """Our absolute path as webdav4 wants it: relative to base_url, no leading slash."""
        return normalized.lstrip("/")

    def _cache_get(self, normalized: str) -> list[WebDavEntry] | None:
        with _cache_lock:
            hit = _listing_cache.get(normalized)
            if hit is None:
                return None
            stored_at, entries = hit
            if self._clock() - stored_at >= CACHE_TTL_SECONDS:
                del _listing_cache[normalized]
                return None
            # A copy: callers sort listings (SPEC 8.3 wants siblings newest-first) and an
            # in-place sort would reorder the shared cache for everyone else.
            return list(entries)

    def _cache_put(self, normalized: str, entries: list[WebDavEntry]) -> None:
        with _cache_lock:
            # Store a copy too, so the list handed back on a cache *miss* is the caller's
            # own and mutating it cannot corrupt what the next reader sees.
            _listing_cache[normalized] = (self._clock(), list(entries))

    def _to_entry(self, item: Any, exclude: str | None = None) -> WebDavEntry | None:
        """Convert one webdav4 dict to a WebDavEntry, or None if its path is unusable.

        webdav4 returns `name` as a path relative to base_url, so it becomes absolute by
        prefixing '/'. A server-side name we cannot normalize is skipped rather than
        allowed to break the whole listing. `exclude` drops the listed directory itself,
        which some servers include in its own listing.
        """
        if not isinstance(item, dict):
            return None

        raw_name = item.get("name")
        if not isinstance(raw_name, str) or not raw_name:
            return None

        try:
            absolute = normalize_path("/" + raw_name.lstrip("/"))
        except ValueError:
            logger.warning("Skipping WebDAV entry with unusable path: %r", raw_name)
            return None

        if exclude is not None and absolute == exclude:
            return None

        return WebDavEntry(
            path=absolute,
            name=absolute.rsplit("/", 1)[-1],
            is_dir=item.get("type") == "directory",
            size_bytes=_as_int(item.get("content_length")),
            modified=_as_utc(item.get("modified")),
            content_type=_as_str(item.get("content_type")),
            etag=_as_str(item.get("etag")),
        )


def _as_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _as_utc(value: Any) -> datetime | None:
    """Guarantee exactly one datetime convention leaves this module: aware UTC.

    webdav4 yields aware UTC for `getlastmodified`, but callers subtract these from
    `utc_now()` for the poller's partial-write guard, and a naive value there raises
    TypeError. Normalising here means no caller has to know or check.
    """
    if not isinstance(value, datetime):
        return None
    return to_utc_aware(value)


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


@contextmanager
def _translate_errors() -> Iterator[None]:
    """Map webdav4 and httpx failures onto the app's error envelope.

    Ordering matters: the specific webdav4 subclasses are caught before the ClientError
    catch-all, and connection-level httpx failures never become webdav4 exceptions at all.
    """
    try:
        yield
    except (httpx.TransportError, BadGatewayError) as exc:
        raise WebDAVUnreachable(
            "Can't reach the WebDAV server. Check that it's up and the URL is right."
        ) from exc
    except ResourceNotFound as exc:
        raise NotFoundError("That file or folder is no longer on the server.") from exc
    except (ResourceAlreadyExists, ResourceConflict) as exc:
        raise WebDAVConflict("Something is already at that location on the server.") from exc
    except ResourceLocked as exc:
        raise WebDAVConflict("That file is locked on the server. Try again shortly.") from exc
    except InsufficientStorage as exc:
        raise WebDAVError("The WebDAV server is out of storage space.") from exc
    except ForbiddenOperation as exc:
        raise WebDAVError(f"The WebDAV server refused that operation: {exc}") from exc
    except ClientError as exc:
        raise WebDAVError(f"The WebDAV server rejected the request: {exc}") from exc
