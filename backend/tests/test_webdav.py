"""Tests for the WebDAV service against a fake client -- never a real server.

The fake mimics webdav4's actual return shape (verified against the installed package):
ls/info yield dicts with `name` relative to base_url, plus `type`, `content_length`,
`modified`, `content_type`, `etag`.
"""

from contextlib import contextmanager
from datetime import datetime
from typing import Any

import httpx
import pytest
from webdav4.client import ResourceAlreadyExists, ResourceNotFound

from app.services.errors import (
    NotFoundError,
    OutsideAllowedRootsError,
    WebDAVConflict,
    WebDAVUnreachable,
)
from app.services.webdav import (
    CACHE_TTL_SECONDS,
    WebDavService,
    parent_of,
)

ROOTS = ["/Documents", "/Trash"]


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeHandle:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._offset = 0
        self.closed = False

    def read(self, size: int) -> bytes:
        chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


class FakeClient:
    """Stands in for webdav4.client.Client. Records calls so tests can assert on them."""

    def __init__(self) -> None:
        self.listings: dict[str, list[dict[str, Any]]] = {}
        self.existing: set[str] = set()
        self.file_bodies: dict[str, bytes] = {}
        self.ls_calls: list[str] = []
        self.move_calls: list[tuple[str, str, bool]] = []
        self.mkdir_calls: list[str] = []
        self.raise_on_ls: Exception | None = None
        self.raise_on_move: Exception | None = None
        self.opened: list[FakeHandle] = []

    def ls(self, path: str, detail: bool = True) -> list[dict[str, Any]]:
        self.ls_calls.append(path)
        if self.raise_on_ls is not None:
            raise self.raise_on_ls
        return self.listings.get(path, [])

    def info(self, path: str) -> dict[str, Any]:
        if path not in self.existing:
            raise ResourceNotFound(path)
        return _entry(path, "file", size=10)

    def exists(self, path: str) -> bool:
        return path in self.existing

    def move(self, from_path: str, to_path: str, overwrite: bool = False) -> None:
        self.move_calls.append((from_path, to_path, overwrite))
        if self.raise_on_move is not None:
            raise self.raise_on_move

    def mkdir(self, path: str) -> None:
        self.mkdir_calls.append(path)
        if path in self.existing:
            raise ResourceAlreadyExists(path)
        self.existing.add(path)

    @contextmanager
    def open(self, path: str, mode: str = "r", **kwargs: Any) -> Any:
        handle = FakeHandle(self.file_bodies.get(path, b""))
        self.opened.append(handle)
        try:
            yield handle
        finally:
            handle.closed = True


def _entry(name: str, kind: str, size: int | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "href": f"/remote.php/dav/files/jonas/{name}",
        "type": kind,
        "content_length": size,
        "created": None,
        "modified": datetime(2026, 8, 21, 12, 0, 0),
        "content_language": None,
        "content_type": "application/pdf" if kind == "file" else None,
        "etag": "abc123",
    }


@pytest.fixture
def fake() -> FakeClient:
    return FakeClient()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def service(fake: FakeClient, clock: FakeClock) -> WebDavService:
    return WebDavService(fake, ROOTS, clock=clock)


class TestParentOf:
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("/Documents/Finance/a.pdf", "/Documents/Finance"),
            ("/Documents/a.pdf", "/Documents"),
            ("/Documents", "/"),
            ("/", "/"),
        ],
    )
    def test_parent(self, path: str, expected: str) -> None:
        assert parent_of(path) == expected


class TestListing:
    def test_converts_webdav4_shape_to_entries(
        self, service: WebDavService, fake: FakeClient
    ) -> None:
        fake.listings["Documents"] = [
            _entry("Documents/a.pdf", "file", size=42),
            _entry("Documents/Finance", "directory"),
        ]

        entries = service.list_dir("/Documents")

        assert [(e.path, e.name, e.is_dir, e.size_bytes) for e in entries] == [
            ("/Documents/a.pdf", "a.pdf", False, 42),
            ("/Documents/Finance", "Finance", True, None),
        ]

    def test_excludes_the_listed_directory_itself(
        self, service: WebDavService, fake: FakeClient
    ) -> None:
        fake.listings["Documents"] = [
            _entry("Documents", "directory"),
            _entry("Documents/a.pdf", "file"),
        ]

        assert [e.path for e in service.list_dir("/Documents")] == ["/Documents/a.pdf"]

    def test_skips_entries_with_unusable_paths(
        self, service: WebDavService, fake: FakeClient
    ) -> None:
        fake.listings["Documents"] = [
            _entry("Documents/../escape", "file"),
            _entry("Documents/fine.pdf", "file"),
        ]

        assert [e.path for e in service.list_dir("/Documents")] == ["/Documents/fine.pdf"]

    def test_list_dirs_only_filters_the_same_listing(
        self, service: WebDavService, fake: FakeClient
    ) -> None:
        fake.listings["Documents"] = [
            _entry("Documents/a.pdf", "file"),
            _entry("Documents/Finance", "directory"),
        ]

        assert [e.name for e in service.list_dirs_only("/Documents")] == ["Finance"]
        # Reuses the cached listing rather than issuing a second PROPFIND.
        service.list_dir("/Documents")
        assert fake.ls_calls == ["Documents"]


class TestCache:
    def test_second_listing_is_served_from_cache(
        self, service: WebDavService, fake: FakeClient
    ) -> None:
        fake.listings["Documents"] = [_entry("Documents/a.pdf", "file")]

        service.list_dir("/Documents")
        service.list_dir("/Documents")

        assert fake.ls_calls == ["Documents"]

    def test_cache_expires_after_the_ttl(
        self, service: WebDavService, fake: FakeClient, clock: FakeClock
    ) -> None:
        fake.listings["Documents"] = [_entry("Documents/a.pdf", "file")]

        service.list_dir("/Documents")
        clock.advance(CACHE_TTL_SECONDS - 1)
        service.list_dir("/Documents")
        assert fake.ls_calls == ["Documents"]

        clock.advance(2)
        service.list_dir("/Documents")
        assert fake.ls_calls == ["Documents", "Documents"]

    def test_move_invalidates_both_containing_directories(
        self, service: WebDavService, fake: FakeClient
    ) -> None:
        fake.listings["Documents/Inbox"] = [_entry("Documents/Inbox/a.pdf", "file")]
        fake.listings["Documents/Filed"] = []
        service.list_dir("/Documents/Inbox")
        service.list_dir("/Documents/Filed")
        assert len(fake.ls_calls) == 2

        service.move("/Documents/Inbox/a.pdf", "/Documents/Filed/a.pdf")

        # Both sides must re-read: the source lost a file and the destination gained one.
        service.list_dir("/Documents/Inbox")
        service.list_dir("/Documents/Filed")
        assert fake.ls_calls == [
            "Documents/Inbox",
            "Documents/Filed",
            "Documents/Inbox",
            "Documents/Filed",
        ]

    def test_mkdir_invalidates_the_parent(self, service: WebDavService, fake: FakeClient) -> None:
        fake.listings["Documents"] = []
        service.list_dir("/Documents")

        service.mkdir("/Documents/New")

        service.list_dir("/Documents")
        assert fake.ls_calls == ["Documents", "Documents"]

    def test_unrelated_directories_stay_cached_across_a_write(
        self, service: WebDavService, fake: FakeClient
    ) -> None:
        fake.listings["Documents/Other"] = []
        fake.listings["Documents"] = []
        service.list_dir("/Documents/Other")
        service.list_dir("/Documents")

        service.mkdir("/Documents/New")

        service.list_dir("/Documents/Other")
        assert fake.ls_calls.count("Documents/Other") == 1


class TestUncachedReads:
    def test_exists_is_never_cached(self, service: WebDavService, fake: FakeClient) -> None:
        """A stale 'no collision' answer would let approve overwrite a file."""
        assert service.exists("/Documents/a.pdf") is False

        fake.existing.add("Documents/a.pdf")

        assert service.exists("/Documents/a.pdf") is True

    def test_stat_reads_live(self, service: WebDavService, fake: FakeClient) -> None:
        fake.existing.add("Documents/a.pdf")

        entry = service.stat("/Documents/a.pdf")

        assert entry.path == "/Documents/a.pdf"
        assert entry.size_bytes == 10

    def test_stat_missing_maps_to_not_found(self, service: WebDavService) -> None:
        with pytest.raises(NotFoundError):
            service.stat("/Documents/missing.pdf")


class TestReadStream:
    def test_yields_chunks_from_a_stream_that_is_still_open(
        self, service: WebDavService, fake: FakeClient
    ) -> None:
        fake.file_bodies["Documents/a.pdf"] = b"abcdefghij"

        chunks = list(service.read_stream("/Documents/a.pdf", chunk_size=4))

        assert chunks == [b"abcd", b"efgh", b"ij"]

    def test_closes_the_handle_when_the_generator_is_exhausted(
        self, service: WebDavService, fake: FakeClient
    ) -> None:
        fake.file_bodies["Documents/a.pdf"] = b"abc"

        list(service.read_stream("/Documents/a.pdf"))

        assert fake.opened[0].closed is True


class TestMove:
    def test_never_permits_overwrite(self, service: WebDavService, fake: FakeClient) -> None:
        service.move("/Documents/a.pdf", "/Documents/b.pdf")

        assert fake.move_calls == [("Documents/a.pdf", "Documents/b.pdf", False)]

    def test_service_exposes_no_way_to_overwrite(self) -> None:
        import inspect

        assert "overwrite" not in inspect.signature(WebDavService.move).parameters

    def test_existing_destination_becomes_a_conflict(
        self, service: WebDavService, fake: FakeClient
    ) -> None:
        fake.raise_on_move = ResourceAlreadyExists("Documents/b.pdf")

        with pytest.raises(WebDAVConflict):
            service.move("/Documents/a.pdf", "/Documents/b.pdf")


class TestMkdirP:
    def test_creates_only_the_missing_levels(
        self, service: WebDavService, fake: FakeClient
    ) -> None:
        fake.existing.add("Documents")

        service.mkdir_p("/Documents/Finance/2026")

        assert fake.mkdir_calls == ["Documents/Finance", "Documents/Finance/2026"]

    def test_is_a_no_op_when_everything_exists(
        self, service: WebDavService, fake: FakeClient
    ) -> None:
        fake.existing.update({"Documents", "Documents/Finance"})

        service.mkdir_p("/Documents/Finance")

        assert fake.mkdir_calls == []


class TestErrorMapping:
    def test_connection_failure_maps_to_unreachable(
        self, service: WebDavService, fake: FakeClient
    ) -> None:
        fake.raise_on_ls = httpx.ConnectError("no route to host")

        with pytest.raises(WebDAVUnreachable):
            service.list_dir("/Documents")

    def test_timeout_maps_to_unreachable(self, service: WebDavService, fake: FakeClient) -> None:
        fake.raise_on_ls = httpx.ConnectTimeout("timed out")

        with pytest.raises(WebDAVUnreachable):
            service.list_dir("/Documents")

    def test_unreachable_carries_a_503(self, service: WebDavService, fake: FakeClient) -> None:
        fake.raise_on_ls = httpx.ConnectError("down")

        with pytest.raises(WebDAVUnreachable) as caught:
            service.list_dir("/Documents")

        assert caught.value.status_code == 503
        assert caught.value.code == "webdav_unreachable"

    def test_missing_directory_maps_to_not_found(
        self, service: WebDavService, fake: FakeClient
    ) -> None:
        fake.raise_on_ls = ResourceNotFound("Documents/nope")

        with pytest.raises(NotFoundError):
            service.list_dir("/Documents")

    def test_a_failed_listing_is_not_cached(self, service: WebDavService, fake: FakeClient) -> None:
        fake.raise_on_ls = httpx.ConnectError("down")
        with pytest.raises(WebDAVUnreachable):
            service.list_dir("/Documents")

        fake.raise_on_ls = None
        fake.listings["Documents"] = [_entry("Documents/a.pdf", "file")]

        assert len(service.list_dir("/Documents")) == 1


class TestPermittedRoots:
    @pytest.mark.parametrize(
        "path",
        ["/etc/passwd", "/Secret/x", "/DocumentsSecret/x"],
        ids=lambda p: f"outside:{p}",
    )
    def test_reads_outside_the_permitted_roots_are_refused(
        self, service: WebDavService, fake: FakeClient, path: str
    ) -> None:
        with pytest.raises(OutsideAllowedRootsError):
            service.list_dir(path)

        # Refused before anything reaches the server.
        assert fake.ls_calls == []

    def test_move_out_of_the_permitted_roots_is_refused(
        self, service: WebDavService, fake: FakeClient
    ) -> None:
        with pytest.raises(OutsideAllowedRootsError):
            service.move("/Documents/a.pdf", "/etc/cron.d/payload")

        assert fake.move_calls == []

    def test_traversal_is_refused_before_reaching_the_server(
        self, service: WebDavService, fake: FakeClient
    ) -> None:
        with pytest.raises(ValueError):
            service.list_dir("/Documents/../../etc")

        assert fake.ls_calls == []

    def test_mkdir_outside_the_permitted_roots_is_refused(
        self, service: WebDavService, fake: FakeClient
    ) -> None:
        with pytest.raises(OutsideAllowedRootsError):
            service.mkdir_p("/etc/evil")

        assert fake.mkdir_calls == []


class TestNoDeletePath:
    def test_the_service_exposes_no_delete_operation(self) -> None:
        """CLAUDE.md rule 1: there is no delete path anywhere in this codebase."""
        surface = {name for name in dir(WebDavService) if not name.startswith("_")}

        assert not surface & {"remove", "delete", "rm", "unlink", "rmdir"}
