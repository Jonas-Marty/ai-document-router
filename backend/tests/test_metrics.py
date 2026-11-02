"""Prometheus metrics (SPEC 6.8): who may scrape, and that the counters follow what happened.

The registry is process-wide and outlives each test, so every assertion is about a change
across the action under test rather than an absolute value.
"""

from collections.abc import Callable, Iterator
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app import db
from app.config import settings as config
from app.jobs import poller
from app.models import AiTask, AppSettings, DocumentStatus, ProposalStatus
from app.services import ai, webdav
from app.services import router as router_service
from app.services.ai import AIUnavailable
from app.services.ai_tasks import ResolvedStep, run_chain
from app.services.audit import Actor
from app.services.errors import WebDAVUnreachable
from tests.test_router import ROOT, TRASH, FakeWebDav, add_proposal, make_document

TOKEN = "scrape-secret-123"
ACTOR = Actor(user_id=None, label="owner@example.com")


def sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def delta(name: str, run: Callable[[], object], /, **labels: str) -> float:
    before = sample(name, **labels)
    run()
    return sample(name, **labels) - before


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
def token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(config, "metrics_token", TOKEN)
    return TOKEN


class TestEndpoint:
    def test_does_not_exist_until_a_token_is_configured(self, client: TestClient) -> None:
        response = client.get("/api/v1/metrics", headers={"Authorization": f"Bearer {TOKEN}"})

        assert response.status_code == 404

    @pytest.mark.parametrize(
        "header", [None, "Bearer wrong-token", f"Basic {TOKEN}", TOKEN, "Bearer "]
    )
    def test_refuses_anything_but_the_bearer_token(
        self, anonymous_client: TestClient, token: str, header: str | None
    ) -> None:
        headers = {"Authorization": header} if header is not None else {}

        response = anonymous_client.get("/api/v1/metrics", headers=headers)

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthenticated"

    def test_serves_the_exposition_format_without_a_session(
        self, anonymous_client: TestClient, token: str
    ) -> None:
        response = anonymous_client.get(
            "/api/v1/metrics", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert "docrouter_analyses_total" in response.text
        # Primed at zero, so a ratio alert has a denominator on a quiet instance.
        assert 'docrouter_analyses_total{outcome="failed"} 0.0' in response.text

    def test_counts_documents_from_the_database_at_scrape_time(
        self, anonymous_client: TestClient, token: str
    ) -> None:
        with Session(db.engine) as s:
            make_document(s, "a.pdf")
            failed = make_document(s, "b.pdf")
            failed.proposal_status = ProposalStatus.failed
            filed = make_document(s, "c.pdf")
            filed.status = DocumentStatus.moved
            s.add_all([failed, filed])
            s.commit()

        text = anonymous_client.get(
            "/api/v1/metrics", headers={"Authorization": f"Bearer {token}"}
        ).text

        assert 'docrouter_documents{status="pending"} 2.0' in text
        assert 'docrouter_documents{status="moved"} 1.0' in text
        assert 'docrouter_documents{status="trashed"} 0.0' in text
        # Only the two still waiting for review count towards the queue.
        assert 'docrouter_queue_proposals{proposal_status="ready"} 1.0' in text
        assert 'docrouter_queue_proposals{proposal_status="failed"} 1.0' in text


class TestHttp:
    def test_labels_requests_by_route_template_not_raw_path(self, client: TestClient) -> None:
        route = "/documents/{document_id}"

        change = delta(
            "docrouter_http_requests_total",
            lambda: client.get("/api/v1/documents/no-such-document"),
            method="GET",
            route=route,
            status="404",
        )

        assert change == 1
        assert (
            sample(
                "docrouter_http_requests_total",
                method="GET",
                route="/documents/no-such-document",
                status="404",
            )
            == 0
        )

    def test_paths_no_route_matched_share_one_label(self, client: TestClient) -> None:
        change = delta(
            "docrouter_http_requests_total",
            lambda: client.get("/wp-admin/setup.php"),
            method="GET",
            route="unmatched",
            status="404",
        )

        assert change == 1


class TestPipeline:
    def test_an_approve_counts_one_moved_document(self, session: Session) -> None:
        document = make_document(session)
        add_proposal(session, document)
        app_settings = session.get(AppSettings, 1)
        assert app_settings is not None

        change = delta(
            "docrouter_documents_routed_total",
            lambda: router_service.approve(
                session,
                FakeWebDav(),  # type: ignore[arg-type]
                app_settings,
                document.id,
                final_name="2026.08.21 Invoice",
                final_folder_path=ROOT,
                document_date=date(2026, 8, 21),
                actor=ACTOR,
            ),
            action="moved",
        )

        assert change == 1

    def test_a_tick_without_settings_counts_as_skipped(self, anonymous_client: TestClient) -> None:
        # anonymous_client points db.engine at an empty database: no settings row yet.
        with Session(db.engine) as s:
            row = s.get(AppSettings, 1)
            if row is not None:
                s.delete(row)
                s.commit()

        change = delta("docrouter_poller_ticks_total", poller.run_once, outcome="skipped")

        assert change == 1


def step(name: str) -> ResolvedStep:
    return ResolvedStep(
        endpoint_name=name, endpoint_url=f"https://{name}/v1", api_key=None, model_name="m"
    )


class TestAi:
    def test_reported_tokens_are_counted_per_task_and_step(self) -> None:
        body = {
            "choices": [{"message": {"content": "# Page 1"}}],
            "usage": {"prompt_tokens": 1200, "completion_tokens": 80},
        }
        meter = ai.UsageMeter()

        def call() -> None:
            transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=body))
            with httpx.Client(transport=transport) as http:
                ai.request_completion(
                    endpoint_url="https://ai.example.com/v1",
                    model_name="vl",
                    api_key=None,
                    system_prompt="s",
                    prompt="p",
                    client=http,
                    usage=meter.scope("extraction", "Metrics test · vl"),
                )

        labels = {"task": "extraction", "step": "Metrics test · vl"}
        before_prompt = sample("docrouter_ai_tokens_total", kind="prompt", **labels)
        change = delta("docrouter_ai_tokens_total", call, kind="completion", **labels)

        assert change == 80
        assert sample("docrouter_ai_tokens_total", kind="prompt", **labels) - before_prompt == 1200

    def test_a_fallback_and_an_exhausted_chain_are_both_counted(self) -> None:
        def unreachable(s: ResolvedStep) -> str:
            raise AIUnavailable(f"{s.endpoint_name} is down.")

        def exhaust() -> None:
            with pytest.raises(AIUnavailable):
                run_chain([step("m-local"), step("m-cloud")], unreachable, AiTask.filing)

        fallbacks_before = sample("docrouter_ai_fallbacks_total", task="filing")
        change = delta("docrouter_ai_chain_exhausted_total", exhaust, task="filing")

        assert change == 1
        # Moving from the first step to the second is a fallback; running out after the last
        # is not another one.
        assert sample("docrouter_ai_fallbacks_total", task="filing") - fallbacks_before == 1
        assert (
            sample(
                "docrouter_ai_requests_total",
                task="filing",
                step="m-cloud · m",
                outcome="unavailable",
            )
            >= 1
        )


class TestWebDav:
    def test_an_unreachable_server_flips_the_gauge_and_is_counted(self) -> None:
        def fail() -> None:
            with pytest.raises(WebDAVUnreachable), webdav._translate_errors("stat", "/x"):
                raise httpx.ConnectError("refused")

        change = delta(
            "docrouter_webdav_operations_total", fail, operation="stat", outcome="unreachable"
        )

        assert change == 1
        assert sample("docrouter_webdav_reachable") == 0

        with webdav._translate_errors("stat", "/x"):
            pass
        assert sample("docrouter_webdav_reachable") == 1
