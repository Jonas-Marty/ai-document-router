"""Approve, skip, trash, revert -- SPEC 6.4.

The invariants that matter most: a failed move writes nothing, a collision never overwrites,
and the client cannot misreport what the AI suggested.
"""

from collections.abc import Iterator
from datetime import date, datetime

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import (
    AppSettings,
    Document,
    DocumentStatus,
    HistoryAction,
    HistoryEntry,
    OcrStatus,
    Proposal,
    ProposalStatus,
)
from app.services import router as router_service
from app.services import searchable
from app.services.errors import (
    FilenameCollision,
    NotRevertible,
    OutsideAllowedRootsError,
    ValidationError,
    WebDAVConflict,
    WebDAVUnreachable,
)
from app.services.extraction import sha256

WATCH = "/Test-Inbox"
ROOT = "/Test-Outbox"
TRASH = "/Test-Trash"


class FakeWebDav:
    """Records what actually reached the server, so tests can assert nothing was touched."""

    def __init__(self) -> None:
        self.existing: set[str] = set()
        self.moves: list[tuple[str, str]] = []
        self.mkdirs: list[str] = []
        self.fail_move: Exception | None = None
        self.replaced: list[tuple[str, bytes, str | None]] = []
        self.fail_replace: Exception | None = None

    def replace(self, path: str, data: bytes, content_type: str | None = None) -> None:
        if self.fail_replace is not None:
            raise self.fail_replace
        self.replaced.append((path, data, content_type))

    def exists(self, path: str) -> bool:
        return path in self.existing

    def mkdir_p(self, path: str) -> None:
        self.mkdirs.append(path)
        self.existing.add(path)

    def move(self, source: str, destination: str) -> None:
        if self.fail_move is not None:
            raise self.fail_move
        self.moves.append((source, destination))
        self.existing.discard(source)
        self.existing.add(destination)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(AppSettings(id=1, allowed_root_folders=[ROOT], trash_folder_path=TRASH))
        s.commit()
        yield s


@pytest.fixture
def app_settings(session: Session) -> AppSettings:
    found = session.get(AppSettings, 1)
    assert found is not None
    return found


@pytest.fixture
def webdav() -> FakeWebDav:
    return FakeWebDav()


def make_document(session: Session, name: str = "scan.pdf") -> Document:
    document = Document(
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
    return document


def add_proposal(
    session: Session,
    document: Document,
    name: str = "2026.08.21 Swisscom Rechnung",
    folder: str = ROOT,
    document_date: date | None = date(2026, 8, 21),
) -> Proposal:
    proposal = Proposal(
        document_id=document.id,
        suggested_name=name,
        target_folder_path=folder,
        document_date=document_date,
        confidence_score=0.9,
        reasoning_text="Invoice header.",
        model_name="llama3.1",
        created_at=datetime(2026, 8, 21, 9, 2),
    )
    session.add(proposal)
    session.commit()
    return proposal


class TestApprove:
    def test_moves_the_file_and_records_history(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        document = make_document(session)
        add_proposal(session, document)

        result, entry = router_service.approve(
            session,
            webdav,
            app_settings,
            document.id,  # type: ignore[arg-type]
            final_name="2026.08.21 Swisscom Rechnung",
            final_folder_path=ROOT,
            document_date=date(2026, 8, 21),
        )

        assert webdav.moves == [(f"{WATCH}/scan.pdf", f"{ROOT}/2026.08.21 Swisscom Rechnung.pdf")]
        assert result.status == DocumentStatus.moved
        assert result.webdav_path == f"{ROOT}/2026.08.21 Swisscom Rechnung.pdf"
        assert entry.final_filename == "2026.08.21 Swisscom Rechnung.pdf"
        assert entry.final_folder_path == ROOT
        assert entry.source_folder_path == WATCH
        assert entry.action == HistoryAction.moved
        assert entry.revertible is True

    def test_carries_the_extension_from_the_source_file(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        document = make_document(session, "scan.PDF")

        _, entry = router_service.approve(
            session,
            webdav,
            app_settings,
            document.id,  # type: ignore[arg-type]
            final_name="Renamed",
            final_folder_path=ROOT,
            document_date=None,
        )

        assert entry.final_filename == "Renamed.pdf"

    def test_creates_the_target_folder(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        document = make_document(session)

        router_service.approve(
            session,
            webdav,
            app_settings,
            document.id,  # type: ignore[arg-type]
            final_name="Named",
            final_folder_path=f"{ROOT}/2026",
            document_date=None,
        )

        assert f"{ROOT}/2026" in webdav.mkdirs

    def test_collision_returns_409_without_touching_the_file(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        document = make_document(session)
        webdav.existing.add(f"{ROOT}/Taken.pdf")

        with pytest.raises(FilenameCollision) as caught:
            router_service.approve(
                session,
                webdav,
                app_settings,
                document.id,  # type: ignore[arg-type]
                final_name="Taken",
                final_folder_path=ROOT,
                document_date=None,
            )

        assert caught.value.status_code == 409
        assert caught.value.code == "filename_collision"
        assert webdav.moves == []
        session.refresh(document)
        assert document.status == DocumentStatus.pending
        assert session.exec(select(HistoryEntry)).all() == []

    def test_a_server_side_collision_also_reports_filename_collision(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        """Overwrite: F is the real guarantee. If the clash appears between the check and
        the move, the form's blocking state must still key on the same code."""
        document = make_document(session)
        webdav.fail_move = WebDAVConflict("Something is already at that location.")

        with pytest.raises(FilenameCollision):
            router_service.approve(
                session,
                webdav,
                app_settings,
                document.id,  # type: ignore[arg-type]
                final_name="Named",
                final_folder_path=ROOT,
                document_date=None,
            )

    def test_a_failed_move_writes_nothing(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        """SPEC 6.4 step 6: the document stays pending so the user can retry."""
        document = make_document(session)
        webdav.fail_move = RuntimeError("network died mid-move")

        with pytest.raises(RuntimeError):
            router_service.approve(
                session,
                webdav,
                app_settings,
                document.id,  # type: ignore[arg-type]
                final_name="Named",
                final_folder_path=ROOT,
                document_date=None,
            )

        session.refresh(document)
        assert document.status == DocumentStatus.pending
        assert document.webdav_path == f"{WATCH}/scan.pdf"
        assert session.exec(select(HistoryEntry)).all() == []

    @pytest.mark.parametrize(
        "folder",
        [WATCH, TRASH, "/etc", "/Elsewhere"],
        ids=["watch-folder", "trash-folder", "outside", "unknown"],
    )
    def test_rejects_targets_outside_the_allowed_roots(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings, folder: str
    ) -> None:
        """The WebDAV service's permitted set also contains the trash and watch folders.
        Approving into the watch folder would have the poller re-ingest the file, putting
        the document back in the queue forever."""
        document = make_document(session)

        with pytest.raises(OutsideAllowedRootsError):
            router_service.approve(
                session,
                webdav,
                app_settings,
                document.id,  # type: ignore[arg-type]
                final_name="Named",
                final_folder_path=folder,
                document_date=None,
            )

        assert webdav.moves == []

    @pytest.mark.parametrize(
        "name", ["", "   ", "bad/name", "bad|name", "..", "x" * 201, ".leading", "trailing-"]
    )
    def test_rejects_invalid_names(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings, name: str
    ) -> None:
        document = make_document(session)

        with pytest.raises(ValidationError):
            router_service.approve(
                session,
                webdav,
                app_settings,
                document.id,  # type: ignore[arg-type]
                final_name=name,
                final_folder_path=ROOT,
                document_date=None,
            )

        assert webdav.moves == []

    def test_an_already_filed_document_cannot_be_approved_twice(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        document = make_document(session)
        router_service.approve(
            session,
            webdav,
            app_settings,
            document.id,  # type: ignore[arg-type]
            final_name="Named",
            final_folder_path=ROOT,
            document_date=None,
        )

        with pytest.raises(ValidationError):
            router_service.approve(
                session,
                webdav,
                app_settings,
                document.id,  # type: ignore[arg-type]
                final_name="Again",
                final_folder_path=ROOT,
                document_date=None,
            )


class TestSearchableWriteBack:
    """Filing a scan stores its OCR'd copy in place of the original.

    The order is the safety argument: the MOVE happens first, unchanged, with its collision
    check and Overwrite: F intact, and only then is the file *we just put there* replaced.
    Nothing is deleted, and nothing someone else put anywhere is ever written over.
    """

    HASH = "c" * 64

    def _ready_scan(self, session: Session) -> Document:
        document = make_document(session)
        document.content_hash = self.HASH
        document.ocr_status = OcrStatus.ready
        session.add(document)
        session.commit()
        searchable.store(self.HASH, b"%PDF-1.7 searchable")
        return document

    def _approve(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings, document: Document
    ) -> None:
        router_service.approve(
            session,
            webdav,
            app_settings,
            document.id,
            final_name="2026.08.21 Swisscom Rechnung",
            final_folder_path=ROOT,
            document_date=date(2026, 8, 21),
        )

    def test_replaces_the_filed_file_after_the_move(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        document = self._ready_scan(session)

        self._approve(session, webdav, app_settings, document)

        destination = f"{ROOT}/2026.08.21 Swisscom Rechnung.pdf"
        assert webdav.moves == [(f"{WATCH}/scan.pdf", destination)]
        # The path replaced is the move's destination, not the source and not anything else.
        assert webdav.replaced == [(destination, b"%PDF-1.7 searchable", "application/pdf")]

    def test_updates_the_record_to_describe_what_is_actually_there(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        document = self._ready_scan(session)

        self._approve(session, webdav, app_settings, document)

        session.refresh(document)
        assert document.content_hash == sha256(b"%PDF-1.7 searchable")
        assert document.file_size_bytes == len(b"%PDF-1.7 searchable")
        # It has a text layer now, so a revert back into the queue has nothing left to OCR.
        assert document.ocr_status == OcrStatus.not_needed

    def test_drops_the_cached_copy_once_it_is_filed(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        """Keyed by the *original* hash. Discarding after content_hash is reassigned would
        look for a file that never existed and leave the real entry behind forever."""
        document = self._ready_scan(session)

        self._approve(session, webdav, app_settings, document)

        assert searchable.load(self.HASH) is None

    def test_a_failed_upload_does_not_fail_the_approve(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        """By this point the document is filed. Losing a text layer is not a reason to tell
        someone their filing did not happen -- what stays behind is the original, correctly
        placed, which is exactly what would have been filed before this feature existed."""
        document = self._ready_scan(session)
        webdav.fail_replace = WebDAVUnreachable("Can't reach the WebDAV server.")

        self._approve(session, webdav, app_settings, document)

        session.refresh(document)
        assert document.status == DocumentStatus.moved
        assert document.webdav_path == f"{ROOT}/2026.08.21 Swisscom Rechnung.pdf"
        assert session.exec(select(HistoryEntry)).one().action == HistoryAction.moved
        # The record still describes the original, because the original is what is there.
        assert document.content_hash == self.HASH

    def test_files_the_original_when_the_setting_is_off(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        document = self._ready_scan(session)
        app_settings.store_ocr_text = False
        session.add(app_settings)
        session.commit()

        self._approve(session, webdav, app_settings, document)

        assert webdav.replaced == []
        assert webdav.moves != []

    def test_files_the_original_when_no_copy_was_ever_made(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        """A document that already had a text layer is the ordinary case, and it must reach
        the server byte-for-byte as it arrived."""
        document = make_document(session)

        self._approve(session, webdav, app_settings, document)

        assert webdav.replaced == []

    def test_files_the_original_when_the_cache_entry_has_gone(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        """The volume was wiped, or prune got there first. The move still stands."""
        document = self._ready_scan(session)
        searchable.discard(self.HASH)

        self._approve(session, webdav, app_settings, document)

        assert webdav.replaced == []
        session.refresh(document)
        assert document.status == DocumentStatus.moved

    def test_trashing_drops_the_cached_copy(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        """Nobody is going to file it. The document itself is in the trash folder, intact --
        this is cache housekeeping, not the WebDAV delete rule 1 forbids."""
        document = self._ready_scan(session)

        router_service.trash(session, webdav, app_settings, document.id)

        assert searchable.load(self.HASH) is None
        assert webdav.replaced == []
        assert webdav.moves == [(f"{WATCH}/scan.pdf", f"{TRASH}/scan.pdf")]


class TestWasOverridden:
    def test_false_when_the_user_accepts_the_suggestion(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        document = make_document(session)
        add_proposal(session, document, name="Suggested", folder=ROOT)

        _, entry = router_service.approve(
            session,
            webdav,
            app_settings,
            document.id,  # type: ignore[arg-type]
            final_name="Suggested",
            final_folder_path=ROOT,
            document_date=date(2026, 8, 21),
        )

        assert entry.was_overridden is False

    def test_a_trailing_slash_is_not_an_override(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        document = make_document(session)
        add_proposal(session, document, name="Suggested", folder=ROOT)

        _, entry = router_service.approve(
            session,
            webdav,
            app_settings,
            document.id,  # type: ignore[arg-type]
            final_name="Suggested",
            final_folder_path=f"{ROOT}/",
            document_date=date(2026, 8, 21),
        )

        assert entry.was_overridden is False

    @pytest.mark.parametrize(
        ("name", "folder", "doc_date"),
        [
            ("Different", ROOT, date(2026, 8, 21)),
            ("Suggested", f"{ROOT}/2026", date(2026, 8, 21)),
            ("Suggested", ROOT, date(2020, 1, 1)),
            ("Suggested", ROOT, None),
        ],
        ids=["name", "folder", "date", "date-cleared"],
    )
    def test_true_when_the_user_changes_anything(
        self,
        session: Session,
        webdav: FakeWebDav,
        app_settings: AppSettings,
        name: str,
        folder: str,
        doc_date: date | None,
    ) -> None:
        document = make_document(session)
        add_proposal(session, document, name="Suggested", folder=ROOT)

        _, entry = router_service.approve(
            session,
            webdav,
            app_settings,
            document.id,  # type: ignore[arg-type]
            final_name=name,
            final_folder_path=folder,
            document_date=doc_date,
        )

        assert entry.was_overridden is True

    def test_snapshots_what_the_ai_said_not_what_the_user_chose(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        """SPEC 5: the client cannot lie about the suggestion, because it never sends it."""
        document = make_document(session)
        add_proposal(session, document, name="AI Name", folder=ROOT)

        _, entry = router_service.approve(
            session,
            webdav,
            app_settings,
            document.id,  # type: ignore[arg-type]
            final_name="Human Name",
            final_folder_path=ROOT,
            document_date=None,
        )

        assert entry.suggestion_snapshot["suggested_name"] == "AI Name"
        assert entry.final_filename == "Human Name.pdf"

    def test_no_proposal_means_nothing_was_overridden(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        document = make_document(session)

        _, entry = router_service.approve(
            session,
            webdav,
            app_settings,
            document.id,  # type: ignore[arg-type]
            final_name="Typed By Hand",
            final_folder_path=ROOT,
            document_date=None,
        )

        assert entry.was_overridden is False
        assert entry.suggestion_snapshot == {}

    def test_the_users_corrected_date_lands_on_the_proposal(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        """Revert keeps the proposal, so the correction has to survive a revert."""
        document = make_document(session)
        add_proposal(session, document, document_date=date(2026, 8, 21))

        router_service.approve(
            session,
            webdav,
            app_settings,
            document.id,  # type: ignore[arg-type]
            final_name="Named",
            final_folder_path=ROOT,
            document_date=date(2026, 1, 2),
        )

        proposal = session.exec(select(Proposal)).one()
        assert proposal.document_date == date(2026, 1, 2)


class TestSkip:
    def test_increments_and_touches_nothing_on_the_server(
        self, session: Session, webdav: FakeWebDav
    ) -> None:
        document = make_document(session)

        result = router_service.skip(session, document.id)

        assert result.status == DocumentStatus.skipped
        assert result.skip_count == 1
        assert webdav.moves == []

    def test_counts_up_across_skips(self, session: Session) -> None:
        document = make_document(session)

        router_service.skip(session, document.id)
        result = router_service.skip(session, document.id)

        assert result.skip_count == 2


class TestTrash:
    def test_moves_to_the_trash_folder(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        document = make_document(session)

        result, entry = router_service.trash(session, webdav, app_settings, document.id)  # type: ignore[arg-type]

        assert webdav.moves == [(f"{WATCH}/scan.pdf", f"{TRASH}/scan.pdf")]
        assert result.status == DocumentStatus.trashed
        assert entry.action == HistoryAction.trashed

    def test_suffixes_on_collision_rather_than_failing(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        """SPEC 6.4: making the user rename something they are discarding would be odd."""
        document = make_document(session)
        webdav.existing.add(f"{TRASH}/scan.pdf")

        _, entry = router_service.trash(session, webdav, app_settings, document.id)  # type: ignore[arg-type]

        assert entry.final_filename != "scan.pdf"
        assert entry.final_filename.startswith("scan_")
        assert entry.final_filename.endswith(".pdf")
        # History must record the name actually written, or revert looks for the wrong file.
        assert webdav.moves[0][1] == f"{TRASH}/{entry.final_filename}"

    def test_requires_a_configured_trash_folder(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        app_settings.trash_folder_path = ""
        session.add(app_settings)
        session.commit()
        document = make_document(session)

        with pytest.raises(ValidationError, match="trash"):
            router_service.trash(session, webdav, app_settings, document.id)  # type: ignore[arg-type]


class TestRevert:
    def _approve(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> tuple[Document, HistoryEntry]:
        document = make_document(session)
        add_proposal(session, document)
        return router_service.approve(
            session,
            webdav,
            app_settings,
            document.id,  # type: ignore[arg-type]
            final_name="Filed",
            final_folder_path=ROOT,
            document_date=None,
        )

    def test_puts_the_file_back_and_requeues_the_document(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        document, entry = self._approve(session, webdav, app_settings)
        webdav.moves.clear()

        reverted_entry, reverted_document = router_service.revert(session, webdav, entry.id)  # type: ignore[arg-type]

        assert webdav.moves == [(f"{ROOT}/Filed.pdf", f"{WATCH}/scan.pdf")]
        assert reverted_document.status == DocumentStatus.pending
        assert reverted_document.webdav_path == f"{WATCH}/scan.pdf"
        assert reverted_entry.revertible is False

    def test_resets_skip_count_but_keeps_the_proposal(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        document = make_document(session)
        add_proposal(session, document)
        router_service.skip(session, document.id)
        _, entry = router_service.approve(
            session,
            webdav,
            app_settings,
            document.id,  # type: ignore[arg-type]
            final_name="Filed",
            final_folder_path=ROOT,
            document_date=None,
        )

        _, reverted = router_service.revert(session, webdav, entry.id)  # type: ignore[arg-type]

        assert reverted.skip_count == 0
        assert session.exec(select(Proposal)).one() is not None

    def test_cannot_revert_twice(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        _, entry = self._approve(session, webdav, app_settings)
        router_service.revert(session, webdav, entry.id)  # type: ignore[arg-type]

        with pytest.raises(NotRevertible):
            router_service.revert(session, webdav, entry.id)  # type: ignore[arg-type]

    def test_a_moved_file_flips_revertible_off(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        """SPEC 6.4: so the UI stops offering an action that cannot work."""
        _, entry = self._approve(session, webdav, app_settings)
        webdav.existing.discard(f"{ROOT}/Filed.pdf")
        webdav.moves.clear()

        with pytest.raises(NotRevertible):
            router_service.revert(session, webdav, entry.id)  # type: ignore[arg-type]

        session.refresh(entry)
        assert entry.revertible is False
        assert webdav.moves == []

    def test_unknown_entry_is_not_revertible(self, session: Session, webdav: FakeWebDav) -> None:
        with pytest.raises(NotRevertible):
            router_service.revert(session, webdav, "does-not-exist")  # type: ignore[arg-type]

    def test_refuses_when_another_document_holds_the_original_path(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        """webdav_path is unique; without this check the commit raises IntegrityError and
        the user sees a 500 instead of a readable conflict."""
        _, entry = self._approve(session, webdav, app_settings)
        make_document(session, "scan.pdf")  # a new scan arrived at the same path

        with pytest.raises(NotRevertible, match="already tracked"):
            router_service.revert(session, webdav, entry.id)  # type: ignore[arg-type]

    def test_a_trashed_document_can_be_reverted(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        document = make_document(session)
        _, entry = router_service.trash(session, webdav, app_settings, document.id)  # type: ignore[arg-type]
        webdav.moves.clear()

        _, reverted = router_service.revert(session, webdav, entry.id)  # type: ignore[arg-type]

        assert reverted.status == DocumentStatus.pending
        assert webdav.moves == [(f"{TRASH}/scan.pdf", f"{WATCH}/scan.pdf")]
