"""The AI usage page's figures (services/ai_usage.py): what is stored per call and how a
period is summed."""

from collections.abc import Iterator
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app import db
from app.models import AiCall, AiConfiguration, AiEndpoint, AiTask
from app.services import ai, ai_usage
from app.services.connections import ResolvedStep
from tests.conftest import signed_in_user_id

OWNER = "owner-1"


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def step(endpoint_id: str = "ep", name: str = "Tower") -> ResolvedStep:
    return ResolvedStep(
        endpoint_name=name,
        endpoint_url="http://ai.test/v1",
        api_key=None,
        model_name="qwen3",
        endpoint_id=endpoint_id,
    )


def at(session: Session, when: datetime) -> None:
    """Move every stored call to `when`, since store() stamps them with now."""
    for call in session.exec(select(AiCall)).all():
        call.occurred_at = when
        session.add(call)
    session.commit()


class TestStore:
    def test_one_row_per_call_with_what_it_is_grouped_by(self, session: Session) -> None:
        meter = ai.UsageMeter()
        step().usage(meter, AiTask.filing).record_failure(1500)
        step("hosted", "Hosted").usage(meter, AiTask.filing).record(
            ai.TokenUsage(800, 40, cached_tokens=300), 900
        )

        ai_usage.store(session, [meter], user_id=OWNER, document_id="doc")
        session.commit()

        failed, answered = sorted(session.exec(select(AiCall)).all(), key=lambda c: c.failed)[::-1]
        assert (failed.connection, failed.failed, failed.fallback) == ("Tower", 1, 0)
        # Nothing falls back since configurations replaced chains (SPEC 6.3a).
        assert (answered.connection, answered.model, answered.fallback) == ("Hosted", "qwen3", 0)
        assert (answered.prompt_tokens, answered.cached_tokens) == (800, 300)
        assert answered.feature == "filing"
        assert failed.run_id == answered.run_id

    def test_a_comparison_is_its_own_feature(self, session: Session) -> None:
        meter = ai.UsageMeter()
        step().usage(meter, AiTask.extraction).record(ai.TokenUsage(10, 1), 5)

        ai_usage.store(session, [meter], user_id=OWNER, document_id="doc", comparison=True)
        session.commit()

        assert session.exec(select(AiCall.feature)).one() == "comparison"


class TestReport:
    def test_sums_per_day_and_counts_runs_once(self, session: Session) -> None:
        session.add(
            AiEndpoint(
                id="ep", user_id=OWNER, name="Tower", base_url="x", created_at=datetime(2026, 1, 1)
            )
        )
        session.add(
            AiConfiguration(
                user_id=OWNER,
                name="Daily",
                filing_endpoint_id="ep",
                filing_model_name="qwen3",
                created_at=datetime(2026, 1, 1),
            )
        )
        for _ in range(2):
            meter = ai.UsageMeter()
            step().usage(meter, AiTask.extraction).record(ai.TokenUsage(100, 10), 1000)
            step().usage(meter, AiTask.filing).record(None, 3000)
            ai_usage.store(session, [meter], user_id=OWNER, document_id="doc")
        session.commit()
        at(session, datetime(2026, 10, 31, 23, 59))

        report = ai_usage.report(session, OWNER, date(2026, 10, 1), date(2026, 10, 31))

        assert report.analyses == 2
        extraction, filing = sorted(report.rows, key=lambda r: r.feature)
        assert extraction.day == "2026-10-31"
        assert (extraction.requests, extraction.prompt_tokens, extraction.duration_ms) == (
            2,
            200,
            2000,
        )
        assert (filing.without_usage, filing.timed) == (2, 2)
        # Configured means "still in a configuration", whichever step uses it.
        assert filing.connection_exists and filing.model_configured

    def test_leaves_out_other_accounts_and_other_days(self, session: Session) -> None:
        for user_id in (OWNER, "someone-else"):
            meter = ai.UsageMeter()
            step().usage(meter, AiTask.filing).record(ai.TokenUsage(1, 1), 1)
            ai_usage.store(session, [meter], user_id=user_id, document_id=None)
        session.commit()
        at(session, datetime(2026, 11, 1, 0, 0))

        assert ai_usage.report(session, OWNER, date(2026, 10, 1), date(2026, 10, 31)).rows == []
        (row,) = ai_usage.report(session, OWNER, date(2026, 11, 1), date(2026, 11, 1)).rows
        assert row.connection_exists is False  # no such endpoint, e.g. deleted since

    def test_rejects_a_backwards_period(self, session: Session) -> None:
        with pytest.raises(ai_usage.ValidationError):
            ai_usage.report(session, OWNER, date(2026, 2, 1), date(2026, 1, 1))


class TestApi:
    def test_reports_the_signed_in_accounts_usage(self, client: TestClient) -> None:
        with Session(db.engine) as session:
            meter = ai.UsageMeter()
            step().usage(meter, AiTask.filing).record(ai.TokenUsage(70, 7), 10)
            ai_usage.store(session, [meter], user_id=signed_in_user_id(), document_id=None)
            session.commit()

        today = date.today()
        # A day either side: rows are dated in UTC, `today` is local.
        period = {
            "start": (today - timedelta(days=1)).isoformat(),
            "end": (today + timedelta(days=1)).isoformat(),
        }

        body = client.get("/api/v1/ai/usage", params=period)

        assert body.status_code == 200, body.text
        assert body.json()["analyses"] == 1
        (row,) = body.json()["rows"]
        assert (row["feature"], row["prompt_tokens"], row["connection"]) == ("filing", 70, "Tower")

    def test_needs_both_ends_of_the_period(self, client: TestClient) -> None:
        assert client.get("/api/v1/ai/usage", params={"start": "2026-01-01"}).status_code == 422
