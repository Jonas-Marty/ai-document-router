"""Analysing documents with configurations (SPEC 6.3a).

What is pinned here is what replaced the chains: a configuration is the whole answer to how a
document is read and filed, nothing quietly tries another connection or reading method, an
unreachable connection makes a document wait rather than fail, and a reading is stored so
that changing only the filing step never reads the pages again.
"""

import hashlib
from collections.abc import Iterator
from datetime import date, datetime
from typing import Any

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import (
    AiConfiguration,
    AiEndpoint,
    AiTask,
    AppSettings,
    AuditAction,
    AuditEvent,
    Document,
    DocumentStatus,
    FilingStrategy,
    OcrStatus,
    Proposal,
    ProposalStatus,
    Reading,
    ReadingMethod,
)
from app.schemas import DocumentRead
from app.services import ai, analysis, extraction, naming, searchable, transcribe
from app.services.connections import ResolvedStep
from app.services.extraction import NO_TEXT_LAYER_MESSAGE, ExtractedDocument
from app.services.times import utc_now

# Rows need an owner; foreign keys are not enforced here, so no user row is needed.
OWNER = "owner-1"
FLAT_TEXT = "Swisscom Rechnung flat text " * 5


class FakeService:
    """Stands in for WebDavService at the boundary CLAUDE.md prescribes for mocking."""

    def __init__(self) -> None:
        self.read_paths: list[str] = []

    def list_dir(self, path: str) -> list[Any]:
        return []

    def read_stream(self, path: str, chunk_size: int = 65536) -> Iterator[bytes]:
        self.read_paths.append(path)
        yield b"%PDF original"


@pytest.fixture
def session() -> Iterator[Session]:
    """Two connections and a default configuration that files on the first."""
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
        for endpoint_id, name in (("ep", "Local"), ("gpu", "Tower")):
            s.add(
                AiEndpoint(
                    user_id=OWNER,
                    id=endpoint_id,
                    name=name,
                    base_url=f"http://{endpoint_id}.test/v1",
                    created_at=utc_now(),
                )
            )
        s.add(
            AiConfiguration(
                user_id=OWNER,
                id="daily",
                name="Daily",
                is_default=True,
                filing_endpoint_id="ep",
                filing_model_name="m",
                created_at=utc_now(),
            )
        )
        s.commit()
        yield s


@pytest.fixture
def app_settings(session: Session) -> AppSettings:
    found = session.get(AppSettings, 1)
    assert found is not None
    return found


@pytest.fixture(autouse=True)
def text_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every document has a usable text layer unless a test says otherwise."""
    monkeypatch.setattr(extraction, "extract", lambda *a, **k: extracted(FLAT_TEXT))


def extracted(text: str, error: str | None = None) -> ExtractedDocument:
    return ExtractedDocument(
        content_hash="h",
        file_size_bytes=100,
        mime_type="application/pdf",
        page_count=1,
        text=text,
        text_error=error,
    )


def add_document(session: Session, name: str = "scan.pdf", **overrides: Any) -> Document:
    fields: dict[str, Any] = {
        "user_id": OWNER,
        "webdav_path": f"/Test-Inbox/{name}",
        "original_filename": name,
        "mime_type": "application/pdf",
        "file_size_bytes": 100,
        "content_hash": hashlib.sha256(name.encode()).hexdigest(),
        "scanned_at": datetime(2026, 8, 21, 9, 0),
        "discovered_at": datetime(2026, 8, 21, 9, 1),
        "status": DocumentStatus.pending,
        "proposal_status": ProposalStatus.pending,
    }
    fields.update(overrides)
    document = Document(**fields)
    session.add(document)
    session.commit()
    return document


def configure(session: Session, configuration_id: str = "daily", **changes: Any) -> None:
    configuration = session.get(AiConfiguration, configuration_id)
    assert configuration is not None
    for key, value in changes.items():
        setattr(configuration, key, value)
    session.add(configuration)
    session.commit()


def add_configuration(session: Session, configuration_id: str, **fields: Any) -> None:
    session.add(
        AiConfiguration(
            user_id=OWNER,
            id=configuration_id,
            name=configuration_id.title(),
            created_at=utc_now(),
            **fields,
        )
    )
    session.commit()


def reads_with_vision(session: Session, **changes: Any) -> None:
    configure(
        session,
        reading_method=ReadingMethod.vision,
        reading_endpoint_id="gpu",
        reading_model_name="v",
        **changes,
    )


def proposal(name: str = "2026.08.21 Swisscom Rechnung", model: str = "m") -> ai.Proposal:
    return ai.Proposal(
        suggested_name=name,
        target_folder_path="/Documents",
        document_date=date(2026, 8, 21),
        confidence_score=0.9,
        reasoning_text="Invoice header.",
        model_name=model,
    )


def file_with(monkeypatch: pytest.MonkeyPatch, reply: Any = None) -> list[dict[str, Any]]:
    """Stub the one-call filing request; returns every call's arguments."""
    calls: list[dict[str, Any]] = []

    def fake(**kwargs: Any) -> ai.Proposal:
        calls.append(kwargs)
        if callable(reply):
            return reply(kwargs)  # type: ignore[no-any-return]
        return proposal()

    monkeypatch.setattr(ai, "request_proposal", fake)
    return calls


def read_with(monkeypatch: pytest.MonkeyPatch, reply: Any = "# Swisscom") -> list[dict[str, Any]]:
    """Stub the vision reading; returns every call's arguments."""
    calls: list[dict[str, Any]] = []

    def fake(data: bytes, step: ResolvedStep, **kwargs: Any) -> transcribe.Transcription:
        calls.append({"data": data, "step": step, **kwargs})
        if isinstance(reply, Exception):
            raise reply
        return transcribe.Transcription(markdown=reply, model_label=step.label)

    monkeypatch.setattr(transcribe, "transcribe", fake)
    return calls


def run(session: Session, app_settings: AppSettings, **kwargs: Any) -> tuple[int, int]:
    return analysis.run_batch(session, FakeService(), app_settings, **kwargs)  # type: ignore[arg-type]


def stored(session: Session) -> Proposal:
    return session.exec(select(Proposal)).one()


class TestOneCall:
    def test_stores_the_proposal_and_where_it_came_from(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        document = add_document(session)
        calls = file_with(monkeypatch)

        assert run(session, app_settings) == (1, 0)

        session.refresh(document)
        assert document.proposal_status == ProposalStatus.ready
        assert document.waiting_reason is None
        found = stored(session)
        assert found.suggested_name == "2026.08.21 Swisscom Rechnung"
        assert (found.configuration_name, found.reading_label) == ("Daily", "Text layer")
        assert found.endpoint_name == "Local"
        assert found.system_prompt == ai.SYSTEM_PROMPT
        assert FLAT_TEXT.strip() in (found.prompt_text or "")
        reading = session.get(Reading, document.reading_id)
        assert reading is not None and reading.text == FLAT_TEXT
        assert calls[0]["endpoint_url"] == "http://ep.test/v1"

    def test_sends_the_configurations_own_prompt_instructions_and_parameters(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.models import AiThinking

        configure(
            session,
            filing_system_prompt="File it carefully.",
            filing_instructions="Swisscom goes to /Documents/Telecom.",
            filing_temperature=0.4,
            filing_max_tokens=1024,
            filing_thinking=AiThinking.off,
        )
        add_document(session)
        calls = file_with(monkeypatch)

        run(session, app_settings)

        (call,) = calls
        assert call["system_prompt"] == "File it carefully."
        assert "Swisscom goes to /Documents/Telecom." in call["prompt"]
        parameters = call["parameters"]
        assert (parameters.temperature, parameters.max_tokens) == (0.4, 1024)
        assert parameters.thinking == AiThinking.off
        assert stored(session).system_prompt == "File it carefully."

    def test_the_text_is_cut_to_the_configurations_limit(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configure(session, max_text_chars=1000)
        monkeypatch.setattr(extraction, "extract", lambda *a, **k: extracted("a" * 999 + "XYZ"))
        add_document(session)
        calls = file_with(monkeypatch)

        run(session, app_settings)

        assert "a" * 999 + "X" in calls[0]["prompt"]
        assert "XY" not in calls[0]["prompt"]

    def test_regenerating_replaces_the_previous_proposal(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        document = add_document(session)
        names = iter(["first", "second"])
        file_with(monkeypatch, lambda kwargs: proposal(next(names)))

        run(session, app_settings)
        document.proposal_status = ProposalStatus.pending
        session.add(document)
        session.commit()
        run(session, app_settings)

        # SPEC 4.1: one-to-one with document, replaced wholesale.
        assert [p.suggested_name for p in session.exec(select(Proposal)).all()] == ["second"]


class TestTwoCalls:
    def test_names_then_files_and_keeps_both_messages(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configure(session, filing_strategy=FilingStrategy.two_calls)
        add_document(session)
        fields = naming.NameFields(sender="Swisscom", title="Rechnung")
        reply = ai.FieldsReply(
            document_date=date(2026, 8, 21),
            fields=fields,
            suggested_name="2026.08.21 Swisscom Rechnung",
            confidence_score=0.9,
            reasoning_text="Invoice header.",
        )
        seen: dict[str, dict[str, Any]] = {}

        def request_fields(**kwargs: Any) -> ai.FieldsReply:
            seen["fields"] = kwargs
            return reply

        def request_folder(**kwargs: Any) -> ai.Proposal:
            seen["folder"] = kwargs
            assert kwargs["fields"] is reply
            return proposal()

        monkeypatch.setattr(ai, "request_fields", request_fields)
        monkeypatch.setattr(ai, "request_folder", request_folder)

        assert run(session, app_settings) == (1, 0)

        assert seen["fields"]["system_prompt"] == ai.FIELDS_SYSTEM_PROMPT
        assert seen["folder"]["system_prompt"] == ai.FOLDER_SYSTEM_PROMPT
        assert "Swisscom" in seen["folder"]["prompt"]
        found = stored(session)
        assert analysis.SECOND_CALL in (found.prompt_text or "")
        assert ai.FIELDS_SYSTEM_PROMPT in (found.system_prompt or "")
        assert ai.FOLDER_SYSTEM_PROMPT in (found.system_prompt or "")


class TestFailures:
    def test_a_rejected_reply_fails_readably_and_keeps_what_it_cost(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        document = add_document(session)

        def reject(kwargs: dict[str, Any]) -> ai.Proposal:
            kwargs["usage"].record(ai.TokenUsage(700, 30), 5)
            raise ai.ProposalRejected("The model chose '/etc', which is outside your folders.")

        file_with(monkeypatch, reject)

        assert run(session, app_settings) == (0, 1)

        session.refresh(document)
        assert document.proposal_status == ProposalStatus.failed
        assert "outside your folders" in (document.proposal_error or "")
        assert document.analysis_usage is not None
        assert document.analysis_usage["total_tokens"] == 730
        # Never a half-valid proposal.
        assert session.exec(select(Proposal)).all() == []

    def test_an_error_from_a_connection_that_answered_fails_rather_than_waits(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 500 or a timeout is the model's answer; waiting would retry it every minute."""
        document = add_document(session)

        def broken(kwargs: dict[str, Any]) -> ai.Proposal:
            raise ai.AIUnavailable("The AI endpoint answered 500.")

        file_with(monkeypatch, broken)

        assert run(session, app_settings) == (0, 1)
        session.refresh(document)
        assert document.proposal_status == ProposalStatus.failed
        assert document.waiting_reason is None

    def test_no_configured_roots_fails_with_a_pointer_to_settings(
        self, session: Session, app_settings: AppSettings
    ) -> None:
        """On a fresh install allowed_root_folders is empty; that must be one readable
        message, not a stack trace per document."""
        document = add_document(session)
        app_settings.allowed_root_folders = []

        assert run(session, app_settings) == (0, 1)
        session.refresh(document)
        assert "Settings" in (document.proposal_error or "")

    def test_a_document_nobody_could_ocr_says_why(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ "No text layer found" is true but useless; the OCR failure is the actionable bit."""
        monkeypatch.setattr(
            extraction, "extract", lambda *a, **k: extracted("", NO_TEXT_LAYER_MESSAGE)
        )
        document = add_document(
            session,
            ocr_status=OcrStatus.failed,
            ocr_error="This PDF is encrypted, so it can't be OCR'd.",
        )

        run(session, app_settings)

        session.refresh(document)
        assert document.proposal_error == "This PDF is encrypted, so it can't be OCR'd."


class TestWaiting:
    def test_without_a_configuration_documents_wait_with_a_pointer_to_the_lab(
        self, session: Session, app_settings: AppSettings
    ) -> None:
        session.delete(session.get(AiConfiguration, "daily"))
        session.commit()
        document = add_document(session)

        assert run(session, app_settings) == (0, 0)
        session.refresh(document)
        assert document.proposal_status == ProposalStatus.pending
        assert document.waiting_reason == analysis.WAITING_FOR_CONFIGURATION

    def test_an_unreachable_filing_connection_is_asked_once_and_everything_on_it_waits(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first = add_document(session, "a.pdf")
        second = add_document(session, "b.pdf")
        calls = file_with(monkeypatch)
        probed: list[str] = []

        def probe(step: ResolvedStep) -> bool:
            probed.append(step.endpoint_name)
            return False

        assert run(session, app_settings, probe=probe) == (0, 0)

        assert probed == ["Local"]
        assert calls == []
        for document in (first, second):
            session.refresh(document)
            assert document.proposal_status == ProposalStatus.pending
            assert document.waiting_reason == "Read · waiting for Local to file"

    def test_a_waiting_document_resumes_once_the_connection_is_back(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        document = add_document(session)
        file_with(monkeypatch)
        run(session, app_settings, probe=lambda step: False)

        assert run(session, app_settings, probe=lambda step: True) == (1, 0)
        session.refresh(document)
        assert document.waiting_reason is None
        assert document.proposal_status == ProposalStatus.ready

    def test_a_connection_lost_mid_call_waits_and_stops_the_rest_of_its_group(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first = add_document(session, "a.pdf")
        second = add_document(session, "b.pdf", discovered_at=datetime(2026, 8, 21, 9, 2))

        def unreachable(kwargs: dict[str, Any]) -> ai.Proposal:
            raise ai.Unreachable("Connection refused.")

        calls = file_with(monkeypatch, unreachable)

        assert run(session, app_settings) == (0, 0)

        assert len(calls) == 1
        session.refresh(first)
        session.refresh(second)
        assert first.proposal_status == ProposalStatus.pending
        assert "Connection refused." in (first.waiting_reason or "")
        assert second.waiting_reason == "Read · waiting for Local to file"

    def test_nothing_falls_back_to_another_connection(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Another configuration on a reachable connection is not tried in its place."""
        add_configuration(session, "hosted", filing_endpoint_id="gpu", filing_model_name="big")
        document = add_document(session)
        calls = file_with(monkeypatch)

        run(session, app_settings, probe=lambda step: step.endpoint_id != "ep")

        assert calls == []
        session.refresh(document)
        assert document.waiting_reason == "Read · waiting for Local to file"

    def test_one_connection_being_off_does_not_hold_up_documents_on_another(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        add_configuration(session, "hosted", filing_endpoint_id="gpu", filing_model_name="big")
        on_tower = add_document(session, "a.pdf", configuration_id="hosted")
        on_local = add_document(session, "b.pdf")
        file_with(monkeypatch)

        assert run(session, app_settings, probe=lambda step: step.endpoint_id == "ep") == (1, 0)

        session.refresh(on_tower)
        session.refresh(on_local)
        assert on_local.proposal_status == ProposalStatus.ready
        assert on_tower.waiting_reason == "Read · waiting for Tower to file"

    def test_waiting_documents_are_taken_last(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """So documents that can run are not crowded out of the batch by ones that will
        probably wait again."""
        app_settings.proposal_batch_size = 1
        waiting = add_document(session, "a.pdf", waiting_reason="Read · waiting for Local")
        fresh = add_document(session, "b.pdf", discovered_at=datetime(2026, 8, 21, 9, 5))
        file_with(monkeypatch)

        run(session, app_settings)

        session.refresh(waiting)
        session.refresh(fresh)
        assert fresh.proposal_status == ProposalStatus.ready
        assert waiting.proposal_status == ProposalStatus.pending


class TestReading:
    def test_the_filing_model_is_given_the_vision_reading_not_the_text_layer(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        reads_with_vision(session, reading_instructions="Keep the umlauts.")
        add_document(session)
        reads = read_with(monkeypatch, "# Swisscom\n\n| Total | 42.00 |")
        calls = file_with(monkeypatch)

        assert run(session, app_settings) == (1, 0)

        (read,) = reads
        assert read["step"].label == "Tower · v"
        assert read["instructions"] == "Keep the umlauts."
        assert read["system_prompt"] == transcribe.SYSTEM_PROMPT
        assert "| Total | 42.00 |" in calls[0]["prompt"]
        assert "flat text" not in calls[0]["prompt"]
        assert "Keep the umlauts." not in calls[0]["prompt"]
        assert stored(session).reading_label == "Tower · v"

    def test_a_failed_vision_reading_fails_rather_than_using_the_text_layer(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        reads_with_vision(session)
        document = add_document(session)
        read_with(monkeypatch, ai.AIUnavailable("The model refused the image."))
        calls = file_with(monkeypatch)

        assert run(session, app_settings) == (0, 1)

        assert calls == []
        session.refresh(document)
        assert document.proposal_status == ProposalStatus.failed
        assert "The model refused the image." in (document.proposal_error or "")

    def test_an_unreachable_reading_connection_waits_before_reading(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        reads_with_vision(session)
        document = add_document(session)
        reads = read_with(monkeypatch)

        run(session, app_settings, probe=lambda step: step.endpoint_id != "gpu")

        assert reads == []
        session.refresh(document)
        assert document.waiting_reason == "Waiting for Tower to read"

    def test_a_reading_is_reused_when_only_the_filing_step_changed(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        reads_with_vision(session)
        document = add_document(session)
        reads = read_with(monkeypatch, "# Read once")
        calls = file_with(monkeypatch)
        run(session, app_settings)

        configure(session, filing_model_name="bigger", filing_instructions="Be terse.")
        document.proposal_status = ProposalStatus.pending
        session.add(document)
        session.commit()
        run(session, app_settings)

        assert len(reads) == 1
        assert [call["model_name"] for call in calls] == ["m", "bigger"]
        assert "# Read once" in calls[1]["prompt"]

    def test_changing_the_reading_step_reads_again(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        reads_with_vision(session)
        document = add_document(session)
        reads = read_with(monkeypatch)
        file_with(monkeypatch)
        run(session, app_settings)

        configure(session, reading_instructions="Tables as markdown.")
        document.proposal_status = ProposalStatus.pending
        session.add(document)
        session.commit()
        run(session, app_settings)

        assert len(reads) == 2
        assert len(session.exec(select(Reading)).all()) == 2

    def test_a_reading_migrated_from_before_configurations_is_not_reused(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nobody knows which prompt or model produced it, so no setup can claim it."""
        reads_with_vision(session)
        document = add_document(session)
        session.add(
            Reading(
                user_id=OWNER,
                document_id=document.id,
                setup_key="migrated",
                method=ReadingMethod.vision,
                label="Old · model",
                text="# Old",
                created_at=utc_now(),
            )
        )
        session.commit()
        reads = read_with(monkeypatch)
        file_with(monkeypatch)

        run(session, app_settings)

        assert len(reads) == 1

    def test_a_batch_is_read_in_full_before_any_document_is_filed(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Reading and filing models rarely fit on one GPU together; alternating would swap
        # them for every document.
        reads_with_vision(session)
        add_document(session, "a.pdf")
        add_document(session, "b.pdf", discovered_at=datetime(2026, 8, 21, 9, 2))
        order: list[str] = []
        read_with(monkeypatch)
        monkeypatch.setattr(
            transcribe,
            "transcribe",
            lambda data, step, **kwargs: (
                order.append("read") or transcribe.Transcription("# Read", step.label)
            ),
        )
        file_with(monkeypatch, lambda kwargs: order.append("file") or proposal())

        assert run(session, app_settings) == (2, 0)
        assert order == ["read", "read", "file", "file"]

    def test_the_text_layer_is_read_from_the_ocred_copy_not_the_original(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The cache is the only place the OCR text exists until the document is filed.

        Reading the server copy here would have the app OCR a scan, keep the result, and
        then still tell the person no text layer could be found.
        """
        document = add_document(session, ocr_status=OcrStatus.ready)
        searchable.store(document.content_hash, b"%PDF searchable")
        seen: list[bytes] = []

        def capture(data: bytes, filename: str, mime: str | None = None) -> ExtractedDocument:
            seen.append(data)
            return extracted(FLAT_TEXT)

        monkeypatch.setattr(extraction, "extract", capture)
        file_with(monkeypatch)
        service = FakeService()

        assert analysis.run_batch(session, service, app_settings) == (1, 0)  # type: ignore[arg-type]
        assert seen == [b"%PDF searchable"]
        assert service.read_paths == []

    def test_the_analysis_stores_what_both_steps_cost(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        reads_with_vision(session)
        document = add_document(session)

        def read(data: bytes, step: ResolvedStep, *, meter: Any, **kwargs: Any) -> Any:
            meter.scope(AiTask.extraction, "Tower · v").record(ai.TokenUsage(1200, 300), 5)
            meter.scope(AiTask.extraction, "Tower · v").record(ai.TokenUsage(1100, 250), 5)
            return transcribe.Transcription(markdown="# Swisscom", model_label="Tower · v")

        def propose(kwargs: dict[str, Any]) -> ai.Proposal:
            kwargs["usage"].record(ai.TokenUsage(900, 60), 5)
            return proposal()

        monkeypatch.setattr(transcribe, "transcribe", read)
        file_with(monkeypatch, propose)

        assert run(session, app_settings) == (1, 0)

        session.refresh(document)
        usage = DocumentRead.from_models(document, None).analysis_usage
        assert usage is not None
        assert usage.extraction is not None and usage.extraction.calls == 2
        assert usage.extraction.total_tokens == 2850
        assert usage.filing is not None and usage.filing.model_label == "Local · m"
        assert usage.total_tokens == 3810
        (event,) = session.exec(
            select(AuditEvent).where(AuditEvent.action == AuditAction.document_analyzed)
        ).all()
        assert event.actor_label == "system"
        assert event.details["outcome"] == "ready"
        assert event.details["configuration"] == "Daily"
        assert event.total_tokens == 3810


class TestPickedConfiguration:
    def test_a_document_is_analysed_with_the_configuration_picked_for_it(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        add_configuration(session, "careful", filing_endpoint_id="gpu", filing_model_name="big")
        add_document(session, configuration_id="careful")
        calls = file_with(monkeypatch)

        run(session, app_settings)

        assert (calls[0]["endpoint_url"], calls[0]["model_name"]) == ("http://gpu.test/v1", "big")
        assert stored(session).configuration_name == "Careful"

    def test_a_pick_belonging_to_someone_else_is_ignored(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session.add(
            AiConfiguration(
                user_id="someone-else",
                id="theirs",
                name="Theirs",
                filing_endpoint_id="gpu",
                filing_model_name="big",
                created_at=utc_now(),
            )
        )
        session.commit()
        add_document(session, configuration_id="theirs")
        calls = file_with(monkeypatch)

        run(session, app_settings)

        assert calls[0]["model_name"] == "m"
