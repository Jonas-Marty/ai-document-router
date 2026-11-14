"""The Lab: test sets, runs and scoring (SPEC 6.4a).

What is pinned here: a test case carries the answer it was approved with; the Lab reads only
inside the allowed folders; runs file exactly like the poller does but never show a document
its own answer; readings are reused; a connection that is away makes a result wait; and the
scores follow the owner's rules, with any verdict overrulable.
"""

# ruff: noqa: F811 - pytest fixtures are imported by name and shadowed by parameters

import time
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.deps import get_webdav
from app.main import app
from app.models import (
    AiConfiguration,
    AppSettings,
    Document,
    DocumentStatus,
    HistoryAction,
    HistoryEntry,
    LabReading,
    LabResult,
    LabResultStatus,
    Reading,
    ReadingMethod,
)
from app.models import TestCase as Case
from app.models import TestCaseSource as Source
from app.services import ai, knowledge, lab, naming
from app.services.errors import NotFoundError, OutsideAllowedRootsError, ValidationError
from app.services.times import utc_now
from app.services.webdav import WebDavEntry
from tests.test_analysis import (  # noqa: F401
    OWNER,
    add_configuration,
    add_document,
    app_settings,
    file_with,
    read_with,
    session,
    text_layer,
)

FIELDS = {"sender": "WIR Bank", "account": "738.953-48.5800", "title": "Kontoauszug"}


class FakeService:
    def __init__(self) -> None:
        self.read_paths: list[str] = []
        self.listing: list[WebDavEntry] = []

    def list_dir(self, path: str) -> list[WebDavEntry]:
        return self.listing

    def read_stream(self, path: str, chunk_size: int = 65536):  # type: ignore[no-untyped-def]
        self.read_paths.append(path)
        yield b"%PDF filed"


def filed_document(
    session: Session,
    name: str = "scan.pdf",
    *,
    final: str = "2026.05.13 WIR Bank 738.953-48.5800 Kontoauszug.pdf",
    folder: str = "/Documents/Finance/WIR Bank/2026",
    fields: dict[str, Any] | None = FIELDS,
    user_id: str = OWNER,
) -> Document:
    document = add_document(
        session,
        name,
        user_id=user_id,
        status=DocumentStatus.moved,
        webdav_path=f"{folder}/{final}",
    )
    session.add(
        HistoryEntry(
            document_id=document.id,
            original_filename=name,
            final_filename=final,
            final_folder_path=folder,
            source_folder_path="/Test-Inbox",
            action=HistoryAction.moved,
            was_overridden=False,
            suggestion_snapshot={},
            name_fields=fields,
            processed_at=utc_now(),
            revertible=True,
        )
    )
    session.commit()
    return document


def a_set(session: Session, name: str = "Bank") -> str:
    return lab.create_test_set(session, OWNER, name).id


def proposal_reply(
    name: str = "2026.05.13 WIR Bank 738.953-48.5800 Kontoauszug",
    folder: str = "/Documents/Finance/WIR Bank/2026",
    fields: dict[str, Any] | None = FIELDS,
    when: date | None = date(2026, 5, 13),
) -> ai.Proposal:
    return ai.Proposal(
        suggested_name=name,
        target_folder_path=folder,
        document_date=when,
        confidence_score=0.9,
        reasoning_text="",
        model_name="m",
        fields=naming.clean_fields(fields) if fields else None,
    )


def results(session: Session) -> list[LabResult]:
    return list(session.exec(select(LabResult)).all())


# --- test cases -------------------------------------------------------------------------


class TestAddingDocuments:
    def test_a_done_document_brings_the_answer_it_was_approved_with(
        self, session: Session, app_settings: AppSettings
    ) -> None:
        document = filed_document(session)
        test_set = a_set(session)

        added = lab.add_documents(session, app_settings, test_set, [document.id])

        assert (added.added, added.skipped) == (1, 0)
        case = session.exec(select(Case)).one()
        assert case.source == Source.done
        assert case.path == "/Documents/Finance/WIR Bank/2026/" + (
            "2026.05.13 WIR Bank 738.953-48.5800 Kontoauszug.pdf"
        )
        assert case.expected_name == "2026.05.13 WIR Bank 738.953-48.5800 Kontoauszug"
        assert case.expected_date == date(2026, 5, 13)
        assert case.expected_folder == "/Documents/Finance/WIR Bank/2026"
        assert case.expected_fields is not None
        assert case.expected_fields["account"] == "738.953-48.5800"

    def test_skips_what_is_not_filed_not_theirs_outside_or_already_there(
        self, session: Session, app_settings: AppSettings
    ) -> None:
        filed = filed_document(session, "a.pdf")
        waiting = add_document(session, "b.pdf")
        someone_elses = filed_document(session, "c.pdf", final="c.pdf", user_id="other")
        outside = filed_document(session, "d.pdf", final="d.pdf", folder="/Elsewhere")
        test_set = a_set(session)
        lab.add_documents(session, app_settings, test_set, [filed.id])

        added = lab.add_documents(
            session,
            app_settings,
            test_set,
            [filed.id, waiting.id, someone_elses.id, outside.id, "missing"],
        )

        assert (added.added, added.skipped) == (0, 5)

    def test_a_hand_written_name_is_kept_whole(
        self, session: Session, app_settings: AppSettings
    ) -> None:
        document = filed_document(session, final="Mietvertrag Bern.pdf", fields=None)
        test_set = a_set(session)

        lab.add_documents(session, app_settings, test_set, [document.id])

        case = session.exec(select(Case)).one()
        assert (case.expected_fields, case.expected_date) == (None, None)
        assert case.expected_name == "Mietvertrag Bern"


class TestAddingAFolder:
    def test_adds_the_pdfs_and_images_directly_in_it(
        self, session: Session, app_settings: AppSettings
    ) -> None:
        service = FakeService()
        folder = "/Documents/Tax"
        names = ["2025.03.01 Steueramt Veranlagung.pdf", "scan.jpg", "notes.txt"]
        service.listing = [
            *(WebDavEntry(path=f"{folder}/{name}", name=name, is_dir=False) for name in names),
            WebDavEntry(path=f"{folder}/2024", name="2024", is_dir=True),
        ]
        test_set = a_set(session)

        added = lab.add_folder(session, service, app_settings, test_set, folder)  # type: ignore[arg-type]

        assert (added.added, added.skipped) == (2, 1)
        cases = {case.original_filename: case for case in session.exec(select(Case)).all()}
        tax = cases["2025.03.01 Steueramt Veranlagung.pdf"]
        assert tax.source == Source.archive
        assert (tax.expected_name, tax.expected_date) == (
            "2025.03.01 Steueramt Veranlagung",
            date(2025, 3, 1),
        )
        assert tax.expected_fields is None
        assert tax.expected_folder == folder
        assert cases["scan.jpg"].mime_type.startswith("image/")

    def test_refuses_a_folder_outside_the_allowed_ones(
        self, session: Session, app_settings: AppSettings
    ) -> None:
        test_set = a_set(session)

        with pytest.raises(OutsideAllowedRootsError):
            lab.add_folder(session, FakeService(), app_settings, test_set, "/Private")  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            lab.add_folder(session, FakeService(), app_settings, test_set, "/Documents/../Private")  # type: ignore[arg-type]

    def test_another_users_set_is_not_found(
        self, session: Session, app_settings: AppSettings
    ) -> None:
        other = lab.create_test_set(session, "other", "Theirs")

        with pytest.raises(NotFoundError):
            lab.add_folder(session, FakeService(), app_settings, other.id, "/Documents")  # type: ignore[arg-type]


class TestTestSets:
    def test_names_are_unique_per_user_ignoring_case(self, session: Session) -> None:
        a_set(session, "Bank")
        lab.create_test_set(session, "other", "Bank")

        with pytest.raises(ValidationError):
            a_set(session, "  bank ")

    def test_deleting_a_set_keeps_its_runs(
        self, session: Session, app_settings: AppSettings
    ) -> None:
        test_set = a_set(session)
        lab.add_documents(session, app_settings, test_set, [filed_document(session).id])
        run = lab.start_run(session, OWNER, test_set, ["daily"])

        lab.delete_test_set(session, OWNER, test_set)

        assert session.exec(select(Case)).all() == []
        assert lab.get_run(session, OWNER, run.id).test_set_name == "Bank"
        assert len(results(session)) == 1


# --- scoring ----------------------------------------------------------------------------


def scored(
    *,
    expected_fields: dict[str, Any] | None = FIELDS,
    predicted_fields: dict[str, Any] | None = FIELDS,
    expected_name: str = "2026.05.13 WIR Bank Kontoauszug",
    predicted_name: str = "2026.05.13 WIR Bank Kontoauszug",
    expected_folder: str = "/Documents/Finance/WIR Bank/2026",
    predicted_folder: str | None = "/Documents/Finance/WIR Bank/2026",
    status: LabResultStatus = LabResultStatus.done,
    template: tuple[str, ...] = naming.DEFAULT_TEMPLATE,
) -> dict[str, float]:
    result = LabResult(
        user_id=OWNER,
        run_id="r",
        test_case_id="c",
        position=0,
        configuration_id="daily",
        configuration_name="Daily",
        original_filename="scan.pdf",
        expected_name=expected_name,
        expected_date=date(2026, 5, 13),
        expected_fields=expected_fields,
        expected_folder=expected_folder,
        status=status,
        predicted_name=predicted_name,
        predicted_date=date(2026, 5, 13),
        predicted_fields=predicted_fields,
        predicted_folder=predicted_folder,
    )
    return lab.score(result, template)


class TestScoring:
    def test_ignores_case_spacing_punctuation_and_umlaut_spelling(self) -> None:
        verdicts = scored(
            expected_fields={
                "sender": "Zürcher KB",
                "account": "CH33 0070-0114",
                "title": "Bestätigung",
            },
            predicted_fields={
                "sender": "ZUERCHER kb",
                "account": "ch3300700114",
                "title": "Bestaetigung",
            },
        )

        assert verdicts["sender"] == verdicts["account"] == verdicts["title"] == 1.0

    def test_both_empty_is_right_and_a_guess_where_none_belongs_is_wrong(self) -> None:
        verdicts = scored(predicted_fields={**FIELDS, "period": "Mai 2026"})

        assert verdicts["number"] == 1.0
        assert verdicts["period"] == 0.0

    def test_only_fields_in_the_owners_template_are_scored(self) -> None:
        verdicts = scored(template=("date", "sender", "title"))

        assert set(verdicts) == {"date", "sender", "title", "folder"}

    def test_a_hand_written_answer_is_compared_as_a_whole_name(self) -> None:
        verdicts = scored(
            expected_fields=None,
            expected_name="Mietvertrag Bern",
            predicted_name="mietvertrag-bern",
        )

        assert verdicts == {"name": 1.0, "folder": 1.0}

    @pytest.mark.parametrize(
        "predicted,score",
        [
            ("/Documents/Finance/WIR Bank/2026", 1.0),
            ("/documents/finance/wir bank/2026/", 1.0),
            ("/Documents/Finance/WIR Bank/2025", 0.5),
            ("/Documents/2026/Finance/WIR Bank", 0.0),
            ("/Documents/Finance/WIR Bank", 0.5),
            ("/Documents/Finance/WIR Bank/Karten", 0.5),
            ("/Documents/Insurance", 0.0),
            (None, 0.0),
        ],
    )
    def test_the_folder_earns_half_for_a_year_apart_or_the_right_parent(
        self, predicted: str | None, score: float
    ) -> None:
        assert scored(predicted_folder=predicted)["folder"] == score

    def test_a_failed_result_is_wrong_everywhere(self) -> None:
        verdicts = scored(status=LabResultStatus.failed)

        assert set(verdicts.values()) == {0.0}


# --- runs -------------------------------------------------------------------------------


def started(session: Session, app_settings: AppSettings, *configuration_ids: str) -> str:
    test_set = a_set(session)
    lab.add_documents(session, app_settings, test_set, [filed_document(session).id])
    return lab.start_run(session, OWNER, test_set, list(configuration_ids or ["daily"])).id


def work(
    session: Session,
    app_settings: AppSettings,
    service: FakeService | None = None,
    **kwargs: Any,
) -> int:
    return lab.work(session, service or FakeService(), app_settings, **kwargs)  # type: ignore[arg-type]


class TestStartingARun:
    def test_one_pending_result_per_document_and_configuration(
        self, session: Session, app_settings: AppSettings
    ) -> None:
        add_configuration(session, "careful", filing_endpoint_id="ep", filing_model_name="big")

        run_id = started(session, app_settings, "daily", "careful")

        rows = results(session)
        assert len(rows) == 2
        assert {row.configuration_name for row in rows} == {"Daily", "Careful"}
        assert {row.status for row in rows} == {LabResultStatus.pending}
        assert rows[0].expected_folder == "/Documents/Finance/WIR Bank/2026"
        assert lab.get_run(session, OWNER, run_id).configurations[0]["name"] == "Daily"

    def test_refuses_an_empty_set_and_too_many_configurations(self, session: Session) -> None:
        empty = a_set(session)

        with pytest.raises(ValidationError):
            lab.start_run(session, OWNER, empty, ["daily"])
        with pytest.raises(ValidationError):
            lab.start_run(session, OWNER, empty, [f"c{index}" for index in range(7)])
        with pytest.raises(ValidationError):
            lab.start_run(session, OWNER, empty, [])

    def test_another_users_configuration_is_not_found(
        self, session: Session, app_settings: AppSettings
    ) -> None:
        session.add(
            AiConfiguration(user_id="other", id="theirs", name="Theirs", created_at=utc_now())
        )
        session.commit()
        test_set = a_set(session)
        lab.add_documents(session, app_settings, test_set, [filed_document(session).id])

        with pytest.raises(NotFoundError):
            lab.start_run(session, OWNER, test_set, ["theirs"])


class TestWorking:
    def test_files_each_document_and_scores_it(
        self,
        session: Session,
        app_settings: AppSettings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        file_with(
            monkeypatch,
            lambda _: proposal_reply(folder="/Documents/Finance/WIR Bank/2025"),
        )
        run_id = started(session, app_settings)

        assert work(session, app_settings) == 1

        result = results(session)[0]
        assert result.status == LabResultStatus.done
        assert result.reading_label == "Text layer"
        assert result.predicted_folder == "/Documents/Finance/WIR Bank/2025"
        assert result.verdicts["sender"] == 1.0
        assert result.verdicts["folder"] == 0.5
        run = lab.get_run(session, OWNER, run_id)
        assert run.finished_at is not None
        assert lab.summarise(run, results(session)).status == "done"

    def test_reads_the_filed_file_once_and_reuses_the_reading(
        self,
        session: Session,
        app_settings: AppSettings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        file_with(monkeypatch, lambda _: proposal_reply())
        configuration = session.get(AiConfiguration, "daily")
        assert configuration is not None
        configuration.reading_method = ReadingMethod.vision
        configuration.reading_endpoint_id = "gpu"
        configuration.reading_model_name = "v"
        session.add(configuration)
        session.commit()
        reads = read_with(monkeypatch)
        service = FakeService()
        test_set = a_set(session)
        lab.add_documents(session, app_settings, test_set, [filed_document(session).id])

        lab.start_run(session, OWNER, test_set, ["daily"])
        work(session, app_settings, service)
        lab.start_run(session, OWNER, test_set, ["daily"])
        work(session, app_settings, service)

        assert len(reads) == 1
        assert service.read_paths == [
            "/Documents/Finance/WIR Bank/2026/2026.05.13 WIR Bank 738.953-48.5800 Kontoauszug.pdf"
        ]
        assert len(session.exec(select(LabReading)).all()) == 1

    def test_uses_the_done_documents_own_reading_when_it_has_one(
        self,
        session: Session,
        app_settings: AppSettings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls = file_with(monkeypatch, lambda _: proposal_reply())
        document = filed_document(session)
        plan = lab.configurations.plan(session, session.get(AiConfiguration, "daily"))  # type: ignore[arg-type]
        session.add(
            Reading(
                user_id=OWNER,
                document_id=document.id,
                setup_key=plan.reading_setup(searchable=False).key,
                method=ReadingMethod.text_layer,
                label="Text layer",
                text="Kontoauszug from the stored reading",
                created_at=utc_now(),
            )
        )
        session.commit()
        test_set = a_set(session)
        lab.add_documents(session, app_settings, test_set, [document.id])
        lab.start_run(session, OWNER, test_set, ["daily"])
        service = FakeService()

        work(session, app_settings, service)

        assert service.read_paths == []
        assert "from the stored reading" in calls[0]["prompt"]

    def test_waits_while_the_filing_connection_is_away(
        self,
        session: Session,
        app_settings: AppSettings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        file_with(monkeypatch, lambda _: proposal_reply())
        run_id = started(session, app_settings)

        assert work(session, app_settings, probe=lambda step: False) == 0

        result = results(session)[0]
        assert result.status == LabResultStatus.pending
        assert result.waiting_reason == "Waiting for Local to file"
        status = lab.summarise(lab.get_run(session, OWNER, run_id), results(session))
        assert (status.status, status.waiting_reason) == ("running", "Waiting for Local to file")

    def test_stops_when_its_time_is_up(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        file_with(monkeypatch, lambda _: proposal_reply())
        started(session, app_settings)

        assert work(session, app_settings, deadline=time.monotonic() - 1) == 0
        assert results(session)[0].status == LabResultStatus.pending

    def test_a_rejected_reply_is_a_failed_result_with_the_reason(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def reject(_: Any) -> ai.Proposal:
            raise ai.ProposalRejected("The model chose '/Elsewhere'.")

        file_with(monkeypatch, reject)
        started(session, app_settings)

        work(session, app_settings)

        result = results(session)[0]
        assert (result.status, result.error) == (
            LabResultStatus.failed,
            "The model chose '/Elsewhere'.",
        )
        assert set(result.verdicts.values()) == {0.0}

    def test_cancelling_stops_whats_left(self, session: Session, app_settings: AppSettings) -> None:
        run_id = started(session, app_settings)

        run = lab.cancel_run(session, OWNER, run_id)

        assert results(session)[0].status == LabResultStatus.cancelled
        assert lab.summarise(run, results(session)).status == "cancelled"
        assert work(session, app_settings) == 0


class TestNoOwnAnswer:
    def test_the_document_under_test_is_never_its_own_example(
        self, session: Session, app_settings: AppSettings
    ) -> None:
        document = filed_document(session)
        reading = Reading(
            user_id=OWNER,
            document_id=document.id,
            setup_key="k",
            method=ReadingMethod.text_layer,
            label="Text layer",
            text="WIR Bank Kontoauszug",
            created_at=utc_now(),
        )
        session.add(reading)
        document.reading_id = reading.id
        session.add(document)
        session.commit()

        shown = knowledge.for_prompt(session, app_settings, "WIR Bank", 8)
        hidden = knowledge.for_prompt(
            session, app_settings, "WIR Bank", 8, exclude_document_ids={document.id}
        )

        assert "Kontoauszug" in shown.text
        assert "Documents filed before" not in hidden.text


# --- verdicts and summaries ---------------------------------------------------------------


class TestVerdicts:
    def test_an_override_wins_and_can_be_dropped(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        file_with(monkeypatch, lambda _: proposal_reply(folder="/Documents/Insurance"))
        run_id = started(session, app_settings)
        work(session, app_settings)
        result = results(session)[0]
        run = lab.get_run(session, OWNER, run_id)
        assert lab.summarise(run, results(session)).summaries[0].fully_correct == 0.0

        lab.set_verdict(session, OWNER, result.id, "folder", 1.0)

        summary = lab.summarise(run, results(session)).summaries[0]
        assert summary.accuracy["folder"] == 1.0
        assert summary.fully_correct == 1.0

        lab.set_verdict(session, OWNER, result.id, "folder", None)

        assert lab.summarise(run, results(session)).summaries[0].accuracy["folder"] == 0.0

    def test_half_is_only_for_the_folder_and_unscored_fields_are_refused(
        self, session: Session, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        file_with(monkeypatch, lambda _: proposal_reply())
        started(session, app_settings)
        work(session, app_settings)
        result = results(session)[0]

        with pytest.raises(ValidationError):
            lab.set_verdict(session, OWNER, result.id, "sender", 0.5)
        with pytest.raises(ValidationError):
            lab.set_verdict(session, OWNER, result.id, "name", 1.0)
        with pytest.raises(NotFoundError):
            lab.set_verdict(session, "other", result.id, "sender", 0.0)


# --- API ----------------------------------------------------------------------------------


class TestApi:
    def test_a_set_and_a_run_through_the_routes(self, client: TestClient) -> None:
        created = client.post("/api/v1/lab/test-sets", json={"name": "Bank"})
        assert created.status_code == 201, created.text
        test_set = created.json()
        assert test_set["case_count"] == 0

        assert client.get(f"/api/v1/lab/test-sets/{test_set['id']}").json()["cases"] == []
        refused = client.post(
            "/api/v1/lab/runs", json={"test_set_id": test_set["id"], "configuration_ids": []}
        )
        assert refused.status_code == 422
        assert client.get("/api/v1/lab/runs").json() == []

    def test_adding_takes_documents_or_a_folder_not_both(self, client: TestClient) -> None:
        test_set = client.post("/api/v1/lab/test-sets", json={"name": "Bank"}).json()
        app.dependency_overrides[get_webdav] = lambda: FakeService()
        try:
            response = client.post(
                f"/api/v1/lab/test-sets/{test_set['id']}/cases",
                json={"document_ids": [], "folder_path": "/Documents"},
            )
        finally:
            app.dependency_overrides.pop(get_webdav, None)

        assert response.status_code == 422

    def test_someone_elses_run_is_not_found(self, client: TestClient) -> None:
        assert client.get("/api/v1/lab/runs/nope").status_code == 404
        assert (
            client.put(
                "/api/v1/lab/results/nope/verdict", json={"field": "folder", "score": 1}
            ).status_code
            == 404
        )
