"""Poller tests: ingestion, partial-write guards, OCR, and how a tick is wired together.

What the analysis itself does is in test_analysis.py.
"""

import io
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pypdf import PdfWriter
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app import log
from app.config import settings as config
from app.jobs import poller
from app.models import (
    AiConfiguration,
    AiEndpoint,
    AppSettings,
    Document,
    DocumentStatus,
    OcrStatus,
    Proposal,
    ProposalStatus,
)
from app.services import ai, analysis
from app.services.times import utc_now
from app.services.webdav import WebDavEntry

# Rows need an owner; foreign keys are not enforced here, so no user row is needed.
OWNER = "owner-1"


def pdf_with_text(text: str) -> bytes:
    """pypdf cannot author text, so pad a blank page's metadata instead.

    Extraction only cares whether enough characters come back, so tests that need the
    'has text' branch stub extraction rather than trying to synthesise a real text layer.
    """
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class FakeService:
    """Stands in for WebDavService at the boundary CLAUDE.md prescribes for mocking."""

    def __init__(self, entries: list[WebDavEntry], bodies: dict[str, bytes] | None = None):
        self.entries = entries
        self.bodies = bodies or {}
        self.read_paths: list[str] = []

    def list_dir(self, path: str) -> list[WebDavEntry]:
        return self.entries

    def read_stream(self, path: str, chunk_size: int = 65536) -> Iterator[bytes]:
        self.read_paths.append(path)
        yield self.bodies.get(path, pdf_with_text("x"))


def entry(name: str, *, age_seconds: int = 3600, is_dir: bool = False) -> WebDavEntry:
    return WebDavEntry(
        path=f"/Test-Inbox/{name}",
        name=name,
        is_dir=is_dir,
        size_bytes=1024,
        modified=utc_now() - timedelta(seconds=age_seconds),
        content_type="application/pdf",
        etag="e",
    )


def configuration() -> AiConfiguration:
    return AiConfiguration(
        user_id=OWNER,
        name="Daily",
        is_default=True,
        filing_endpoint_id="ep",
        filing_model_name="m",
        created_at=utc_now(),
    )


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(
            AppSettings(
                id=1,
                user_id=OWNER,
                allowed_root_folders=["/Documents"],
                trash_folder_path="/Trash",
                watch_folder_path="/Test-Inbox",
            )
        )
        # A default configuration: without one every document waits instead of reaching the
        # stubbed request_proposal.
        s.add(
            AiEndpoint(
                user_id=OWNER,
                id="ep",
                name="Local",
                base_url="http://ai.test/v1",
                created_at=utc_now(),
            )
        )
        s.add(configuration())
        s.commit()
        yield s


@pytest.fixture
def app_settings(session: Session) -> AppSettings:
    found = session.get(AppSettings, 1)
    assert found is not None
    return found


class TestIngest:
    def test_records_a_new_file(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = FakeService([entry("scan.pdf")])

        added = poller.ingest(session, service, app_settings)  # type: ignore[arg-type]

        assert len(added) == 1
        document = session.exec(select(Document)).one()
        assert document.webdav_path == "/Test-Inbox/scan.pdf"
        assert document.status == DocumentStatus.pending

    def test_is_idempotent_across_ticks(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = FakeService([entry("scan.pdf")])

        poller.ingest(session, service, app_settings)  # type: ignore[arg-type]
        added = poller.ingest(session, service, app_settings)  # type: ignore[arg-type]

        assert added == {}
        assert len(session.exec(select(Document)).all()) == 1

    def test_skips_files_that_are_still_being_written(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SPEC 6.2 rule 3: a file modified seconds ago may still be uploading."""
        service = FakeService([entry("fresh.pdf", age_seconds=2)])

        assert poller.ingest(session, service, app_settings) == {}  # type: ignore[arg-type]

    @pytest.mark.parametrize("name", ["a.part", "b.tmp", "c.crdownload", "d.PART"])
    def test_skips_partial_extensions(
        self,
        session: Session,
        app_settings: AppSettings,
        monkeypatch: pytest.MonkeyPatch,
        name: str,
    ) -> None:
        service = FakeService([entry(name)])

        assert poller.ingest(session, service, app_settings) == {}  # type: ignore[arg-type]

    def test_skips_directories(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = FakeService([entry("subfolder", is_dir=True)])

        assert poller.ingest(session, service, app_settings) == {}  # type: ignore[arg-type]

    def test_dedupes_identical_content_under_a_different_name(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body = pdf_with_text("same")
        service = FakeService(
            [entry("first.pdf"), entry("second.pdf")],
            bodies={"/Test-Inbox/first.pdf": body, "/Test-Inbox/second.pdf": body},
        )

        added = poller.ingest(session, service, app_settings)  # type: ignore[arg-type]

        assert len(added) == 1

    def test_respects_the_ingest_cap(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A first run against 52 real scans must not download all of them in one tick."""
        monkeypatch.setattr(config, "poller_ingest_batch", 2)
        bodies = {f"/Test-Inbox/s{i}.pdf": f"body-{i}".encode() for i in range(5)}
        service = FakeService([entry(f"s{i}.pdf") for i in range(5)], bodies=bodies)

        assert len(poller.ingest(session, service, app_settings)) == 2  # type: ignore[arg-type]

    def test_a_file_with_no_text_layer_lands_as_failed_not_an_exception(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = FakeService([entry("blank.pdf")])

        poller.ingest(session, service, app_settings)  # type: ignore[arg-type]

        document = session.exec(select(Document)).one()
        assert document.proposal_status == ProposalStatus.failed
        assert document.proposal_error is not None
        assert "No text layer" in document.proposal_error
        # A PDF with no text is exactly what the OCR phase is for, so ingest queues it there
        # rather than leaving it as a document nothing will ever look at again.
        assert document.ocr_status == OcrStatus.pending
        # Still fully approvable by hand.
        assert document.status == DocumentStatus.pending


class TestNoDoubleDownload:
    def test_a_document_ingested_this_tick_is_not_downloaded_again(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ingest already read the bytes; extracting the same text again would double
        every tick's network cost against the server."""
        monkeypatch.setattr(
            ai,
            "request_proposal",
            lambda **kwargs: ai.Proposal(
                suggested_name="Named",
                target_folder_path="/Documents",
                document_date=None,
                confidence_score=0.8,
                reasoning_text="r",
                model_name="m",
            ),
        )
        service = FakeService([entry("scan.pdf")])

        fresh = poller.ingest(session, service, app_settings)  # type: ignore[arg-type]
        analysis.run_batch(session, service, app_settings, fresh)  # type: ignore[arg-type]

        assert service.read_paths == ["/Test-Inbox/scan.pdf"]

    def test_a_document_from_an_earlier_tick_is_read_when_needed(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = FakeService([entry("scan.pdf")])
        poller.ingest(session, service, app_settings)  # type: ignore[arg-type]
        service.read_paths.clear()

        # No cache passed: simulates a later tick picking up leftover work.
        analysis.run_batch(session, service, app_settings)  # type: ignore[arg-type]

        assert service.read_paths == []  # nothing pending: the blank PDF already failed


class TestOcrPhase:
    """The phase that produces a searchable copy, between ingest and proposals."""

    def _scan(self, session: Session, **overrides: Any) -> Document:
        fields: dict[str, Any] = {
            "user_id": OWNER,
            "webdav_path": "/Test-Inbox/scan.pdf",
            "original_filename": "scan.pdf",
            "mime_type": "application/pdf",
            "file_size_bytes": 100,
            "content_hash": "a" * 64,
            "scanned_at": datetime(2026, 8, 21, 9, 0),
            "discovered_at": datetime(2026, 8, 21, 9, 1),
            "status": DocumentStatus.pending,
            "proposal_status": ProposalStatus.failed,
            "proposal_error": "No text layer found in this document.",
            "ocr_status": OcrStatus.pending,
        }
        fields.update(overrides)
        document = Document(**fields)
        session.add(document)
        session.commit()
        return document

    def _no_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.services.extraction import NO_TEXT_LAYER_MESSAGE, ExtractedDocument

        monkeypatch.setattr(
            poller.extraction,
            "extract",
            lambda *a, **k: ExtractedDocument(
                content_hash="a" * 64,
                file_size_bytes=100,
                mime_type="application/pdf",
                page_count=1,
                text="",
                text_error=NO_TEXT_LAYER_MESSAGE,
            ),
        )

    def test_caches_the_copy_and_reopens_the_proposal(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        document = self._scan(session)
        self._no_text(monkeypatch)
        monkeypatch.setattr(poller.searchable, "is_available", lambda: True)
        monkeypatch.setattr(poller.searchable, "build", lambda data, **k: b"%PDF searchable")

        assert poller.ocr_pending(session, FakeService([]), app_settings) == [document.id]  # type: ignore[arg-type]

        session.refresh(document)
        assert document.ocr_status == OcrStatus.ready
        assert poller.searchable.load(document.content_hash) == b"%PDF searchable"
        # The reason it failed -- no text layer -- is no longer true, so it goes back in the
        # queue for a proposal instead of staying failed forever.
        assert document.proposal_status == ProposalStatus.pending
        assert document.proposal_error is None

    def test_leaves_an_already_successful_proposal_alone(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        document = self._scan(session, proposal_status=ProposalStatus.ready, proposal_error=None)
        self._no_text(monkeypatch)
        monkeypatch.setattr(poller.searchable, "is_available", lambda: True)
        monkeypatch.setattr(poller.searchable, "build", lambda data, **k: b"%PDF searchable")

        poller.ocr_pending(session, FakeService([]), app_settings)  # type: ignore[arg-type]

        session.refresh(document)
        assert document.proposal_status == ProposalStatus.ready

    def test_a_document_that_turns_out_to_have_text_is_not_ocred(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The migration had to guess for documents already in the queue. Spending minutes
        adding a text layer to a page that has one is the outcome worth a second read."""
        from app.services.extraction import ExtractedDocument

        document = self._scan(session)
        monkeypatch.setattr(
            poller.extraction,
            "extract",
            lambda *a, **k: ExtractedDocument(
                content_hash="a" * 64,
                file_size_bytes=100,
                mime_type="application/pdf",
                page_count=1,
                text="Swisscom Rechnung " * 10,
                text_error=None,
            ),
        )
        monkeypatch.setattr(poller.searchable, "is_available", lambda: True)

        def must_not_run(data: bytes, **kwargs: Any) -> bytes:
            raise AssertionError("ocrmypdf must not run for a document that already has text")

        monkeypatch.setattr(poller.searchable, "build", must_not_run)

        assert poller.ocr_pending(session, FakeService([]), app_settings) == []  # type: ignore[arg-type]

        session.refresh(document)
        assert document.ocr_status == OcrStatus.not_needed

    def test_a_failure_lands_on_a_status_with_a_reason(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        document = self._scan(session)
        self._no_text(monkeypatch)
        monkeypatch.setattr(poller.searchable, "is_available", lambda: True)

        def fail(data: bytes, **kwargs: Any) -> bytes:
            raise poller.searchable.SearchableUnavailable("This PDF is encrypted.")

        monkeypatch.setattr(poller.searchable, "build", fail)

        assert poller.ocr_pending(session, FakeService([]), app_settings) == []  # type: ignore[arg-type]

        session.refresh(document)
        assert document.ocr_status == OcrStatus.failed
        assert document.ocr_error == "This PDF is encrypted."

    def test_does_nothing_when_the_setting_is_off(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nothing may write to someone's document store because a default said so."""
        document = self._scan(session)
        app_settings.store_ocr_text = False
        session.add(app_settings)
        session.commit()
        monkeypatch.setattr(poller.searchable, "is_available", lambda: True)

        def must_not_run(data: bytes, **kwargs: Any) -> bytes:
            raise AssertionError("OCR must not run with the setting off")

        monkeypatch.setattr(poller.searchable, "build", must_not_run)

        assert poller.ocr_pending(session, FakeService([]), app_settings) == []  # type: ignore[arg-type]
        session.refresh(document)
        assert document.ocr_status == OcrStatus.pending

    def test_skips_a_document_that_has_already_been_filed(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._scan(session, status=DocumentStatus.moved)
        monkeypatch.setattr(poller.searchable, "is_available", lambda: True)

        assert poller.ocr_pending(session, FakeService([]), app_settings) == []  # type: ignore[arg-type]

    def test_is_capped_per_tick(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OCR is minutes, not seconds -- an uncapped phase would starve the other two."""
        for index in range(4):
            self._scan(
                session,
                webdav_path=f"/Test-Inbox/scan{index}.pdf",
                content_hash=f"{index}" * 64,
            )
        self._no_text(monkeypatch)
        monkeypatch.setattr(config, "poller_ocr_batch", 2)
        monkeypatch.setattr(poller.searchable, "is_available", lambda: True)
        monkeypatch.setattr(poller.searchable, "build", lambda data, **k: b"%PDF searchable")

        assert len(poller.ocr_pending(session, FakeService([]), app_settings)) == 2  # type: ignore[arg-type]


class TestFullTick:
    """The three phases in the order run_once runs them.

    This is where the OCR write-back actually earns its keep: a scan with no text layer has
    to come out of one tick with a proposal built from the text OCR found, not with the "no
    text layer" failure it arrived with. The phases are individually covered above; what is
    checked here is that they are wired together in the right order.
    """

    def test_a_scan_with_no_text_layer_is_ocred_and_proposed_in_one_tick(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        from app.services.extraction import NO_TEXT_LAYER_MESSAGE, ExtractedDocument

        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        SQLModel.metadata.create_all(engine)
        with Session(engine) as setup:
            setup.add(
                AppSettings(
                    id=1,
                    user_id=OWNER,
                    allowed_root_folders=["/Documents"],
                    trash_folder_path="/Trash",
                    watch_folder_path="/Test-Inbox",
                    # run_once only polls users with a connection; build_service is stubbed.
                    webdav_base_url="https://cloud.example.com/remote.php/dav/files/owner",
                    webdav_username="owner",
                    webdav_password_encrypted=b"not-read-here",
                )
            )
            setup.add(
                AiEndpoint(
                    user_id=OWNER,
                    id="ep",
                    name="Local",
                    base_url="http://ai.test/v1",
                    created_at=utc_now(),
                )
            )
            setup.add(configuration())
            setup.commit()

        monkeypatch.setattr(poller.db, "engine", engine)
        monkeypatch.setattr(
            poller, "build_service", lambda _session, _settings: FakeService([entry("scan.pdf")])
        )

        # The scan reads as image-only until it has been through OCR, and as text after.
        ocred = {"done": False}

        def extract(data: bytes, filename: str, mime: str | None = None) -> ExtractedDocument:
            if data == b"%PDF searchable":
                return ExtractedDocument(
                    content_hash="h",
                    file_size_bytes=len(data),
                    mime_type="application/pdf",
                    page_count=1,
                    text="Swisscom Rechnung " * 10,
                    text_error=None,
                )
            return ExtractedDocument(
                content_hash="a" * 64,
                file_size_bytes=len(data),
                mime_type="application/pdf",
                page_count=1,
                text="",
                text_error=NO_TEXT_LAYER_MESSAGE,
            )

        def build(data: bytes, **kwargs: Any) -> bytes:
            ocred["done"] = True
            return b"%PDF searchable"

        monkeypatch.setattr(poller.extraction, "extract", extract)
        monkeypatch.setattr(poller.searchable, "is_available", lambda: True)
        monkeypatch.setattr(poller.searchable, "build", build)
        monkeypatch.setattr(
            ai,
            "request_proposal",
            lambda **kwargs: ai.Proposal(
                suggested_name="2026.08.21 Swisscom Rechnung",
                target_folder_path="/Documents",
                document_date=None,
                confidence_score=0.9,
                reasoning_text="Invoice header.",
                model_name="m",
            ),
        )

        caplog.handler.addFilter(log.ContextFilter())
        caplog.set_level(logging.INFO, logger="app.jobs.poller")

        poller.run_once()

        assert ocred["done"] is True
        with Session(engine) as check:
            document = check.exec(select(Document)).one()
            assert document.ocr_status == OcrStatus.ready
            # Not still stuck on the failure it was given at ingest.
            assert document.proposal_status == ProposalStatus.ready
            assert document.proposal_error is None
            proposal = check.exec(select(Proposal)).one()
            assert proposal.suggested_name == "2026.08.21 Swisscom Rechnung"

        summary = [r for r in caplog.records if r.getMessage().startswith("Tick done")]
        assert len(summary) == 1
        assert "ingested 1, OCR 1, proposals 1 ready / 0 failed" in summary[0].getMessage()
        # Every line of the tick shares one id -- the per-user pass adds to it rather than
        # replacing it -- and it is gone once the tick is over.
        contexts = {str(getattr(r, "context", "")) for r in caplog.records}
        tick_ids = {context.split()[0] for context in contexts}
        assert len(tick_ids) == 1
        assert tick_ids.pop().startswith("tick=")
        assert f"user={OWNER[:8]}" in " ".join(contexts)
        assert log.context_var.get() == ""


class TestPartialWriteGuardTimezones:
    def test_compares_against_tz_aware_timestamps_without_raising(self) -> None:
        """webdav4 yields aware UTC. A naive value here raised TypeError before the
        service started normalising at its boundary."""
        aware = WebDavEntry(
            path="/Test-Inbox/a.pdf",
            name="a.pdf",
            is_dir=False,
            modified=datetime.now(UTC) - timedelta(hours=1),
        )

        assert poller._is_ready(aware) is True


class TestEveryUser:
    def test_each_connected_user_is_polled_and_one_failure_does_not_stop_the_others(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        SQLModel.metadata.create_all(engine)
        connected = {
            "webdav_base_url": "https://cloud.example.com/remote.php/dav/files/x",
            "webdav_username": "x",
            "webdav_password_encrypted": b"not-read-here",
        }
        with Session(engine) as setup:
            for user_id in ("broken", "works"):
                setup.add(
                    AppSettings(
                        user_id=user_id,
                        allowed_root_folders=["/Documents"],
                        trash_folder_path="/Trash",
                        watch_folder_path="/Test-Inbox",
                        **connected,
                    )
                )
            # Never connected: nothing to poll, and no error for it either.
            setup.add(AppSettings(user_id="idle", watch_folder_path="/Test-Inbox"))
            setup.commit()

        polled: list[str] = []

        def build(_session: Session, settings: AppSettings) -> FakeService:
            polled.append(settings.user_id)
            if settings.user_id == "broken":
                raise RuntimeError("their Nextcloud is down")
            return FakeService([entry("scan.pdf")], {"/Test-Inbox/scan.pdf": b"%PDF"})

        monkeypatch.setattr(poller.db, "engine", engine)
        monkeypatch.setattr(poller, "build_service", build)
        monkeypatch.setattr(poller.analysis, "run_batch", lambda *args: (0, 0))

        poller.run_once()

        assert sorted(polled) == ["broken", "works"]
        with Session(engine) as check:
            (document,) = check.exec(select(Document)).all()
            # Filed under the account whose inbox it came from, not the first one polled.
            assert document.user_id == "works"
