"""The Activity page's cost figures (services/ai_costs.py): what one analysis cost, what a
filed document cost, and where the money went (SPEC 8.11)."""

from collections.abc import Iterator
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app import db
from app.models import (
    AiCall,
    AiConfiguration,
    AiTask,
    Document,
    DocumentStatus,
    HistoryAction,
    HistoryEntry,
)
from app.services import ai, ai_costs, ai_usage
from app.services.connections import ResolvedStep
from tests.conftest import signed_in_user_id

OWNER = "owner-1"
DAY = datetime(2026, 11, 10, 9, 0)
NOVEMBER = (date(2026, 11, 1), date(2026, 11, 30))


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def call(
    session: Session,
    run_id: str,
    when: datetime,
    *,
    document_id: str | None = "doc",
    outcome: str | None = "ready",
    source: str | None = "analysis",
    feature: str = "filing",
    cost: float | None = 0.01,
    currency: str | None = "CHF",
    configuration_id: str | None = "cfg",
    configuration_name: str | None = "Daily",
    user_id: str = OWNER,
) -> None:
    session.add(
        AiCall(
            user_id=user_id,
            occurred_at=when,
            run_id=run_id,
            document_id=document_id,
            feature=feature,
            connection="Hosted",
            model="big",
            prompt_tokens=100,
            completion_tokens=10,
            cost=cost,
            currency=currency if cost is not None else None,
            source=source,
            outcome=outcome,
            configuration_id=configuration_id,
            configuration_name=configuration_name,
        )
    )
    session.commit()


def document(session: Session, document_id: str, name: str = "scan.pdf") -> None:
    session.add(
        Document(
            id=document_id,
            user_id=OWNER,
            webdav_path=f"/Scans/{document_id}.pdf",
            original_filename=name,
            mime_type="application/pdf",
            file_size_bytes=1,
            page_count=3,
            content_hash=document_id,
            scanned_at=DAY,
            discovered_at=DAY,
            status=DocumentStatus.moved,
        )
    )
    session.commit()


def filed(
    session: Session,
    document_id: str,
    when: datetime,
    *,
    overridden: bool = False,
    configuration_id: str = "cfg",
) -> None:
    session.add(
        HistoryEntry(
            document_id=document_id,
            original_filename="scan.pdf",
            final_filename="2026.11.10 Swisscom Rechnung.pdf",
            final_folder_path="/Finance",
            source_folder_path="/Scans",
            action=HistoryAction.moved,
            was_overridden=overridden,
            suggestion_snapshot={"suggested_name": "x", "configuration_id": configuration_id},
            processed_at=when,
            revertible=True,
        )
    )
    session.commit()


def report(session: Session) -> ai_costs.CostReport:
    return ai_costs.report(session, OWNER, *NOVEMBER)


class TestKinds:
    def test_first_failed_and_re_analysis_of_one_document(self, session: Session) -> None:
        call(session, "a", DAY, cost=0.02)
        call(session, "b", DAY + timedelta(hours=1), outcome="failed", cost=0.01)
        call(session, "c", DAY + timedelta(hours=2), cost=0.03)

        result = report(session)

        assert result.split == pytest.approx(
            {"first": 0.02, "failed": 0.01, "reanalysis": 0.03, "other": 0.0}
        )
        assert result.spent == pytest.approx(0.06)
        assert result.analyses.count == 3
        assert result.analyses.average == pytest.approx(0.02)
        assert (result.analyses.minimum, result.analyses.maximum) == pytest.approx((0.01, 0.03))

    def test_an_analysis_before_the_period_makes_this_one_a_re_analysis(
        self, session: Session
    ) -> None:
        call(session, "old", datetime(2026, 10, 20))
        call(session, "new", DAY)

        result = report(session)

        assert result.split["reanalysis"] == pytest.approx(0.01)
        assert result.split["first"] == 0
        assert result.analyses.count == 1

    def test_a_run_that_waited_is_part_of_the_analysis_it_went_into(self, session: Session) -> None:
        call(session, "read", DAY, outcome="waiting", feature="extraction", cost=0.01)
        call(session, "file", DAY + timedelta(minutes=5), cost=0.02)

        result = report(session)
        page = ai_costs.analyses_page(session, OWNER, *NOVEMBER, 0, 20)

        assert result.analyses.count == 1
        assert result.analyses.average == pytest.approx(0.03)
        assert result.split["first"] == pytest.approx(0.03)
        (row,) = page.items
        assert (row.reading_cost, row.filing_cost, row.cost) == pytest.approx((0.01, 0.02, 0.03))

    def test_lab_comparisons_and_unmatched_calls_are_other_and_not_analyses(
        self, session: Session
    ) -> None:
        call(session, "lab", DAY, source="lab", outcome=None)
        call(session, "cmp", DAY, source="comparison", feature="comparison", outcome=None)
        call(session, "old", DAY, source=None, outcome=None)

        result = report(session)

        assert result.split["other"] == pytest.approx(0.03)
        assert result.analyses.count == 0
        assert result.configurations == []

    def test_an_unpriced_analysis_counts_but_has_no_cost(self, session: Session) -> None:
        call(session, "a", DAY, cost=None)

        result = report(session)

        assert (result.priced, result.analyses.count, result.analyses.priced) == (False, 1, 0)
        assert result.analyses.average is None


class TestCurrencies:
    def test_the_most_used_currency_leads_and_the_others_are_listed_apart(
        self, session: Session
    ) -> None:
        call(session, "a", DAY, document_id="a")
        call(session, "b", DAY, document_id="b")
        call(session, "c", DAY, document_id="c", currency="USD", cost=0.5)

        result = report(session)

        assert result.currency == "CHF"
        assert result.spent == pytest.approx(0.02)
        assert [(o.currency, o.amount) for o in result.others] == [("USD", 0.5)]
        assert result.analyses.priced == 2


class TestFiledAndConfigurations:
    def test_a_filed_document_costs_every_analysis_it_took(self, session: Session) -> None:
        document(session, "doc")
        call(session, "before", datetime(2026, 10, 30), outcome="failed", cost=0.04)
        call(session, "a", DAY, cost=0.02)
        filed(session, "doc", DAY + timedelta(hours=1))

        result = report(session)

        assert result.filed_documents == 1
        assert result.filed.average == pytest.approx(0.06)

    def test_per_configuration_cost_failures_and_approvals(self, session: Session) -> None:
        session.add(
            AiConfiguration(
                id="cfg", user_id=OWNER, name="Renamed", is_default=True, created_at=DAY
            )
        )
        session.commit()
        for name in ("one", "two"):
            document(session, name)
        call(session, "a", DAY, document_id="one", cost=0.02)
        call(session, "b", DAY, document_id="two", cost=0.04)
        call(session, "c", DAY + timedelta(hours=1), document_id="two", outcome="failed")
        filed(session, "one", DAY + timedelta(hours=2))
        filed(session, "two", DAY + timedelta(hours=2), overridden=True)

        (row,) = report(session).configurations

        assert (row.name, row.is_default) == ("Renamed", True)
        assert (row.analyses, row.failed, row.priced) == (3, 1, 3)
        assert row.average == pytest.approx(0.07 / 3)
        assert (row.approvals, row.approved_unchanged) == (2, 1)

    def test_the_most_expensive_documents_with_their_attempts(self, session: Session) -> None:
        document(session, "cheap")
        document(session, "dear", "invoice.pdf")
        call(session, "a", DAY, document_id="cheap", cost=0.01)
        call(session, "b", DAY, document_id="dear", cost=0.02, outcome="failed")
        call(session, "c", DAY + timedelta(hours=1), document_id="dear", cost=0.03)

        expensive = report(session).expensive

        assert [(d.document_id, d.attempts, d.failed) for d in expensive] == [
            ("dear", 2, 1),
            ("cheap", 1, 0),
        ]
        assert expensive[0].cost == pytest.approx(0.05)
        assert expensive[0].name == "invoice.pdf"


class TestAnalysesPage:
    def test_newest_first_with_kind_name_and_paging(self, session: Session) -> None:
        document(session, "doc")
        filed(session, "doc", DAY + timedelta(hours=3))
        call(session, "a", DAY)
        call(session, "b", DAY + timedelta(hours=1), outcome="failed")
        call(session, "c", DAY + timedelta(hours=2))

        page = ai_costs.analyses_page(session, OWNER, *NOVEMBER, 0, 2)
        rest = ai_costs.analyses_page(session, OWNER, *NOVEMBER, 2, 2)

        assert page.total == 3
        assert [(r.run_id, r.kind) for r in page.items] == [
            ("c", "reanalysis"),
            ("b", "failed"),
        ]
        assert [r.run_id for r in rest.items] == ["a"]
        assert page.items[0].document_name == "2026.11.10 Swisscom Rechnung.pdf"
        assert page.items[0].currency == "CHF"

    def test_another_users_analyses_are_not_counted(self, session: Session) -> None:
        call(session, "theirs", DAY, user_id="someone-else")

        assert ai_costs.analyses_page(session, OWNER, *NOVEMBER, 0, 20).total == 0
        assert report(session).analyses.count == 0


class TestStore:
    def test_a_stored_call_carries_its_source_outcome_and_configuration(
        self, session: Session
    ) -> None:
        meter = ai.UsageMeter("cfg", "Daily")
        ResolvedStep(
            endpoint_name="Hosted", endpoint_url="https://h/v1", api_key=None, model_name="m"
        ).usage(meter, AiTask.filing).record(ai.TokenUsage(1, 1), 10)

        ai_usage.store(session, [meter], user_id=OWNER, document_id="doc", outcome="ready")
        session.commit()

        stored = session.exec(select(AiCall)).one()
        assert (stored.source, stored.outcome) == ("analysis", "ready")
        assert (stored.configuration_id, stored.configuration_name) == ("cfg", "Daily")


class TestApi:
    def test_the_report_and_the_analyses_for_a_period(self, client: TestClient) -> None:
        with Session(db.engine) as session:
            call(session, "a", DAY, user_id=signed_in_user_id())
        period = {"start": "2026-11-01", "end": "2026-11-30"}

        body = client.get("/api/v1/ai/costs", params=period).json()
        page = client.get("/api/v1/ai/costs/analyses", params=period).json()

        assert body["currency"] == "CHF"
        assert body["analyses"]["count"] == 1
        assert page["total"] == 1
        assert page["items"][0]["occurred_at"].endswith("+00:00")

    def test_a_period_that_ends_before_it_starts_is_refused(self, client: TestClient) -> None:
        backwards = {"start": "2026-11-30", "end": "2026-11-01"}
        response = client.get("/api/v1/ai/costs", params=backwards)

        assert response.status_code == 422
