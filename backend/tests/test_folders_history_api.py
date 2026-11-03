"""Endpoint tests for /folders, /history, and the routing actions."""

from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import db
from app.deps import get_webdav
from app.main import app
from app.models import (
    AppSettings,
    Document,
    DocumentStatus,
    HistoryAction,
    HistoryEntry,
    ProposalStatus,
)
from app.services.errors import NotFoundError
from app.services.webdav import WebDavEntry
from tests.conftest import signed_in_settings, signed_in_user_id

# Rows need an owner; foreign keys are not enforced here, so no user row is needed.
OWNER = "owner-1"

ROOT = "/Test-Outbox"
TRASH = "/Test-Trash"
WATCH = "/Test-Inbox"


class FakeWebDav:
    def __init__(self) -> None:
        self.listings: dict[str, list[WebDavEntry]] = {}
        self.existing: set[str] = set()
        self.moves: list[tuple[str, str]] = []
        self.mkdirs: list[str] = []

    def list_dir(self, path: str) -> list[WebDavEntry]:
        if path not in self.listings:
            raise NotFoundError("That folder is no longer on the server.")
        return self.listings[path]

    def list_dirs_only(self, path: str) -> list[WebDavEntry]:
        return [entry for entry in self.list_dir(path) if entry.is_dir]

    def exists(self, path: str) -> bool:
        return path in self.existing

    def mkdir_p(self, path: str) -> None:
        self.mkdirs.append(path)
        self.existing.add(path)
        self.listings.setdefault(path, [])

    def move(self, source: str, destination: str) -> None:
        self.moves.append((source, destination))
        self.existing.discard(source)
        self.existing.add(destination)


def folder(path: str) -> WebDavEntry:
    name = path.rsplit("/", 1)[-1]
    return WebDavEntry(path=path, name=name, is_dir=True)


def file_entry(path: str, *, size: int = 100, modified: datetime | None = None) -> WebDavEntry:
    from datetime import UTC

    name = path.rsplit("/", 1)[-1]
    return WebDavEntry(
        path=path,
        name=name,
        is_dir=False,
        size_bytes=size,
        modified=modified or datetime(2026, 8, 21, 12, 0, tzinfo=UTC),
    )


@pytest.fixture
def fake() -> Iterator[FakeWebDav]:
    stub = FakeWebDav()
    app.dependency_overrides[get_webdav] = lambda: stub
    yield stub
    app.dependency_overrides.pop(get_webdav, None)


@pytest.fixture
def configured(client: TestClient) -> TestClient:
    with Session(db.engine) as session:
        settings = signed_in_settings(session)
        settings.allowed_root_folders = [ROOT]
        settings.trash_folder_path = TRASH
        settings.watch_folder_path = WATCH
        session.add(settings)
        session.commit()
    return client


def make_document(name: str = "scan.pdf") -> str:
    with Session(db.engine) as session:
        document = Document(
            user_id=signed_in_user_id(),
            webdav_path=f"{WATCH}/{name}",
            original_filename=name,
            mime_type="application/pdf",
            file_size_bytes=1024,
            page_count=1,
            content_hash=f"hash-{name}",
            scanned_at=datetime(2026, 8, 21, 9, 0),
            discovered_at=datetime(2026, 8, 21, 9, 1),
            status=DocumentStatus.pending,
            proposal_status=ProposalStatus.ready,
        )
        session.add(document)
        session.commit()
        return document.id


class TestFolderTree:
    def test_top_level_is_the_allowed_roots_only(
        self, configured: TestClient, fake: FakeWebDav
    ) -> None:
        """SPEC 8.5: nothing outside the allowed roots is reachable through the picker."""
        fake.listings[ROOT] = [folder(f"{ROOT}/2026"), file_entry(f"{ROOT}/a.pdf")]
        fake.listings[f"{ROOT}/2026"] = []

        body = configured.get("/api/v1/folders/tree").json()

        assert [node["path"] for node in body] == [ROOT]
        assert body[0]["has_children"] is True
        assert body[0]["file_count"] == 1
        assert body[0]["children"] is None

    def test_lists_children_of_a_path(self, configured: TestClient, fake: FakeWebDav) -> None:
        fake.listings[ROOT] = [folder(f"{ROOT}/2026"), folder(f"{ROOT}/2025")]
        fake.listings[f"{ROOT}/2026"] = [file_entry(f"{ROOT}/2026/a.pdf")]
        fake.listings[f"{ROOT}/2025"] = []

        body = configured.get(f"/api/v1/folders/tree?path={ROOT}").json()

        assert sorted(node["path"] for node in body) == [f"{ROOT}/2025", f"{ROOT}/2026"]

    def test_refuses_a_path_outside_the_allowed_roots(
        self, configured: TestClient, fake: FakeWebDav
    ) -> None:
        response = configured.get("/api/v1/folders/tree?path=/etc")

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "outside_allowed_roots"

    def test_reports_when_no_roots_are_configured(
        self, client: TestClient, fake: FakeWebDav
    ) -> None:
        response = client.get("/api/v1/folders/tree")

        assert response.status_code == 422
        assert "Settings" in response.json()["error"]["message"]


class TestCreateFolder:
    def test_creates_under_an_allowed_root(self, configured: TestClient, fake: FakeWebDav) -> None:
        fake.listings[ROOT] = []

        body = configured.post("/api/v1/folders", json={"parent_path": ROOT, "name": "2026"}).json()

        assert body["path"] == f"{ROOT}/2026"
        assert body["name"] == "2026"
        assert f"{ROOT}/2026" in fake.mkdirs

    def test_refuses_an_existing_folder(self, configured: TestClient, fake: FakeWebDav) -> None:
        fake.existing.add(f"{ROOT}/2026")

        response = configured.post("/api/v1/folders", json={"parent_path": ROOT, "name": "2026"})

        assert response.status_code == 409

    @pytest.mark.parametrize("name", ["", "  ", "a/b", "bad|name", "..", "x" * 101])
    def test_refuses_invalid_names(
        self, configured: TestClient, fake: FakeWebDav, name: str
    ) -> None:
        response = configured.post("/api/v1/folders", json={"parent_path": ROOT, "name": name})

        assert response.status_code == 422
        assert fake.mkdirs == []

    def test_refuses_a_parent_outside_the_allowed_roots(
        self, configured: TestClient, fake: FakeWebDav
    ) -> None:
        response = configured.post("/api/v1/folders", json={"parent_path": "/etc", "name": "evil"})

        assert response.status_code == 403
        assert fake.mkdirs == []


class TestFolderContext:
    def test_returns_newest_siblings_first_capped_at_five(
        self, configured: TestClient, fake: FakeWebDav
    ) -> None:
        from datetime import UTC

        fake.listings[ROOT] = [
            file_entry(f"{ROOT}/f{i}.pdf", modified=datetime(2026, 1, i + 1, tzinfo=UTC))
            for i in range(8)
        ]

        body = configured.get(f"/api/v1/folders/context?path={ROOT}").json()

        assert body["exists"] is True
        assert body["total_file_count"] == 8
        assert len(body["siblings"]) == 5
        assert [s["filename"] for s in body["siblings"]] == [
            "f7.pdf",
            "f6.pdf",
            "f5.pdf",
            "f4.pdf",
            "f3.pdf",
        ]

    def test_flags_a_filename_collision(self, configured: TestClient, fake: FakeWebDav) -> None:
        fake.listings[ROOT] = [file_entry(f"{ROOT}/taken.pdf")]

        taken = configured.get(f"/api/v1/folders/context?path={ROOT}&filename=taken.pdf").json()
        free = configured.get(f"/api/v1/folders/context?path={ROOT}&filename=free.pdf").json()

        assert taken["filename_collision"] is True
        assert free["filename_collision"] is False

    def test_a_folder_that_does_not_exist_yet_is_not_an_error(
        self, configured: TestClient, fake: FakeWebDav
    ) -> None:
        """The form shows "will be created" rather than an error card."""
        body = configured.get(f"/api/v1/folders/context?path={ROOT}/new").json()

        assert body["exists"] is False
        assert body["siblings"] == []
        assert body["filename_collision"] is False

    def test_excludes_subfolders_from_the_sibling_list(
        self, configured: TestClient, fake: FakeWebDav
    ) -> None:
        fake.listings[ROOT] = [folder(f"{ROOT}/2026"), file_entry(f"{ROOT}/a.pdf")]

        body = configured.get(f"/api/v1/folders/context?path={ROOT}").json()

        assert [s["filename"] for s in body["siblings"]] == ["a.pdf"]
        assert body["total_file_count"] == 1


class TestRoutingEndpoints:
    def test_approve_returns_document_and_history_entry(
        self, configured: TestClient, fake: FakeWebDav
    ) -> None:
        document_id = make_document()

        body = configured.post(
            f"/api/v1/documents/{document_id}/approve",
            json={"final_name": "Filed Name", "final_folder_path": ROOT, "document_date": None},
        ).json()

        assert body["document"]["status"] == "moved"
        assert body["history_entry"]["final_filename"] == "Filed Name.pdf"
        assert body["history_entry"]["revertible"] is True

    def test_approve_collision_is_409(self, configured: TestClient, fake: FakeWebDav) -> None:
        document_id = make_document()
        fake.existing.add(f"{ROOT}/Taken.pdf")

        response = configured.post(
            f"/api/v1/documents/{document_id}/approve",
            json={"final_name": "Taken", "final_folder_path": ROOT, "document_date": None},
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "filename_collision"
        assert fake.moves == []

    def test_skip_advances_the_count(self, configured: TestClient, fake: FakeWebDav) -> None:
        document_id = make_document()

        body = configured.post(f"/api/v1/documents/{document_id}/skip").json()

        assert body["document"]["status"] == "skipped"
        assert body["document"]["skip_count"] == 1

    def test_trash_moves_and_records(self, configured: TestClient, fake: FakeWebDav) -> None:
        document_id = make_document()

        body = configured.post(f"/api/v1/documents/{document_id}/trash").json()

        assert body["document"]["status"] == "trashed"
        assert body["history_entry"]["action"] == "trashed"
        assert fake.moves == [(f"{WATCH}/scan.pdf", f"{TRASH}/scan.pdf")]


class TestWatchFolderBoundary:
    def test_approve_refuses_a_watch_folder_inside_an_allowed_root(
        self, configured: TestClient, fake: FakeWebDav
    ) -> None:
        # Filed there, the poller would ingest it again on the next tick, forever.
        with Session(db.engine) as session:
            settings = signed_in_settings(session)
            settings.watch_folder_path = f"{ROOT}/Inbox"
            session.add(settings)
            session.commit()
        document_id = make_document()

        response = configured.post(
            f"/api/v1/documents/{document_id}/approve",
            json={
                "final_name": "Filed",
                "final_folder_path": f"{ROOT}/Inbox",
                "document_date": None,
            },
        )

        assert response.status_code == 422
        assert fake.moves == []

    def test_documents_from_a_previous_watch_folder_stay_reachable(
        self, configured: TestClient
    ) -> None:
        from app.services import settings as settings_service

        open_id = make_document("open.pdf")
        filed_id = make_document("filed.pdf")
        with Session(db.engine) as session:
            filed = session.get(Document, filed_id)
            assert filed is not None
            filed.webdav_path = "/Elsewhere/filed.pdf"
            filed.status = DocumentStatus.moved
            session.add(filed)
            settings = signed_in_settings(session)
            settings.watch_folder_path = "/New-Inbox"
            session.add(settings)
            session.commit()
            assert session.get(Document, open_id) is not None

            roots = settings_service.permitted_roots(session, settings)

        # Still queued from the old folder: approvable where it is. Already filed: not a
        # reason to widen anything.
        assert WATCH in roots
        assert "/New-Inbox" in roots
        assert "/Elsewhere" not in roots


class TestHistory:
    def _entry(self, session: Session, name: str, processed_at: datetime) -> HistoryEntry:
        document = Document(
            user_id=signed_in_user_id(),
            webdav_path=f"{ROOT}/{name}",
            original_filename=name,
            mime_type="application/pdf",
            file_size_bytes=1,
            content_hash=f"h-{name}",
            scanned_at=datetime(2026, 8, 21, 9, 0),
            discovered_at=datetime(2026, 8, 21, 9, 1),
            status=DocumentStatus.moved,
        )
        session.add(document)
        session.commit()
        entry = HistoryEntry(
            document_id=document.id,
            original_filename=name,
            final_filename=name,
            final_folder_path=ROOT,
            source_folder_path=WATCH,
            action=HistoryAction.moved,
            was_overridden=False,
            suggestion_snapshot={},
            processed_at=processed_at,
            revertible=True,
        )
        session.add(entry)
        session.commit()
        return entry

    def test_empty_history(self, configured: TestClient) -> None:
        body = configured.get("/api/v1/history").json()

        assert body == {"items": [], "next_cursor": None}

    def test_newest_first(self, configured: TestClient) -> None:
        with Session(db.engine) as session:
            self._entry(session, "old.pdf", datetime(2026, 1, 1, 10, 0))
            self._entry(session, "new.pdf", datetime(2026, 6, 1, 10, 0))

        body = configured.get("/api/v1/history").json()

        assert [i["original_filename"] for i in body["items"]] == ["new.pdf", "old.pdf"]

    def test_paginates_without_skipping_ties(self, configured: TestClient) -> None:
        """processed_at is not unique -- approving two documents in the same second is
        ordinary, and a timestamp-only cursor drops or repeats rows at the boundary."""
        same_instant = datetime(2026, 5, 1, 12, 0, 0)
        with Session(db.engine) as session:
            for i in range(5):
                self._entry(session, f"tie{i}.pdf", same_instant)

        seen: list[str] = []
        cursor = None
        for _ in range(5):
            url = "/api/v1/history?limit=2" + (f"&cursor={cursor}" if cursor else "")
            page = configured.get(url).json()
            seen += [i["id"] for i in page["items"]]
            cursor = page["next_cursor"]
            if cursor is None:
                break

        assert len(seen) == 5
        assert len(set(seen)) == 5

    def test_rejects_a_malformed_cursor(self, configured: TestClient) -> None:
        response = configured.get("/api/v1/history?cursor=garbage")

        assert response.status_code == 422

    def test_revert_puts_the_document_back_in_the_queue(
        self, configured: TestClient, fake: FakeWebDav
    ) -> None:
        document_id = make_document()
        approved = configured.post(
            f"/api/v1/documents/{document_id}/approve",
            json={"final_name": "Filed", "final_folder_path": ROOT, "document_date": None},
        ).json()
        entry_id = approved["history_entry"]["id"]
        fake.moves.clear()

        body = configured.post(f"/api/v1/history/{entry_id}/revert").json()

        assert body["document"]["status"] == "pending"
        assert body["history_entry"]["revertible"] is False
        assert fake.moves == [(f"{ROOT}/Filed.pdf", f"{WATCH}/scan.pdf")]

        # Back in the queue, which is the point of revert.
        queue = configured.get("/api/v1/queue").json()
        assert [i["id"] for i in queue["items"]] == [document_id]

    def test_reverting_twice_is_409(self, configured: TestClient, fake: FakeWebDav) -> None:
        document_id = make_document()
        approved = configured.post(
            f"/api/v1/documents/{document_id}/approve",
            json={"final_name": "Filed", "final_folder_path": ROOT, "document_date": None},
        ).json()
        entry_id = approved["history_entry"]["id"]
        configured.post(f"/api/v1/history/{entry_id}/revert")

        response = configured.post(f"/api/v1/history/{entry_id}/revert")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "not_revertible"

    def test_history_records_the_full_round_trip(
        self, configured: TestClient, fake: FakeWebDav
    ) -> None:
        document_id = make_document()
        configured.post(
            f"/api/v1/documents/{document_id}/approve",
            json={"final_name": "Filed", "final_folder_path": ROOT, "document_date": None},
        )

        items = configured.get("/api/v1/history").json()["items"]

        assert len(items) == 1
        assert items[0]["document_id"] == document_id
        assert items[0]["final_folder_path"] == ROOT
        assert items[0]["processed_at"].endswith("+00:00")


class TestNoDeleteEndpointExists:
    def test_no_route_deletes_anything(self) -> None:
        """CLAUDE.md rule 1: trash means moving to the trash folder."""
        for route in app.router.routes:
            methods = getattr(route, "methods", set()) or set()
            path = getattr(route, "path", "")
            assert "DELETE" not in methods, f"{path} exposes DELETE"


class TestPromptContext:
    """The folder tree and example filenames sent to the AI with every proposal."""

    @pytest.fixture
    def archive(self) -> FakeWebDav:
        # The watch folder and trash living *inside* an allowed root is a common layout,
        # and exactly where unsorted scans used to leak into the naming examples.
        stub = FakeWebDav()
        stub.listings = {
            "/Docs": [folder("/Docs/Inbox"), folder("/Docs/Trash"), folder("/Docs/Finance")],
            "/Docs/Inbox": [file_entry("/Docs/Inbox/scan_0041.pdf")],
            "/Docs/Trash": [file_entry("/Docs/Trash/scan_0007.pdf")],
            "/Docs/Finance": [
                file_entry(
                    "/Docs/Finance/2025.01.13 Green Rechnung 9000001.pdf",
                    modified=datetime(2025, 1, 13),
                ),
                file_entry(
                    "/Docs/Finance/2026.08.13 green Rechnung 9253636.pdf",
                    modified=datetime(2026, 8, 13),
                ),
            ],
        }
        return stub

    def _context(self, stub: FakeWebDav) -> tuple[list[str], list[str]]:
        from app.services import folders

        app_settings = AppSettings(
            id=1,
            user_id=OWNER,
            allowed_root_folders=["/Docs"],
            trash_folder_path="/Docs/Trash",
            watch_folder_path="/Docs/Inbox",
        )
        return folders.prompt_context(stub, app_settings)  # type: ignore[arg-type]

    def test_examples_come_from_filed_documents_not_the_inbox_or_trash(
        self, archive: FakeWebDav
    ) -> None:
        _, samples = self._context(archive)

        assert "scan_0041.pdf" not in samples
        assert "scan_0007.pdf" not in samples
        assert "2026.08.13 green Rechnung 9253636.pdf" in samples

    def test_inbox_and_trash_are_not_offered_as_destinations(self, archive: FakeWebDav) -> None:
        tree, _ = self._context(archive)

        assert tree == ["/Docs", "/Docs/Finance"]

    def test_most_recently_filed_examples_come_first(self, archive: FakeWebDav) -> None:
        # The naming convention drifts; the newest files show the one in use now.
        _, samples = self._context(archive)

        assert samples[0] == "2026.08.13 green Rechnung 9253636.pdf"
