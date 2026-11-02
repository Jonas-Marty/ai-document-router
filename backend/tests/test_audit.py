"""The audit log (SPEC 6.7): what gets recorded, what never does, and who may read it."""

import json
from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app import db
from app.config import settings as config
from app.models import AppSettings, AuditAction, AuditEvent
from app.services import ai, audit
from app.services import router as router_service
from app.services.audit import Actor
from app.services.errors import FilenameCollision
from app.services.times import to_storage, utc_now
from tests.conftest import TEST_EMAIL
from tests.test_router import ROOT, TRASH, FakeWebDav, add_proposal, make_document

ACTOR = Actor(user_id="user-1", label="owner@example.com")


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


def events(session: Session, action: AuditAction | None = None) -> list[AuditEvent]:
    statement = select(AuditEvent)
    if action is not None:
        statement = statement.where(AuditEvent.action == action)
    return list(session.exec(statement).all())


def db_events(action: AuditAction | None = None) -> list[AuditEvent]:
    with Session(db.engine) as session:
        return events(session, action)


class TestSameTransaction:
    def test_an_approve_records_who_filed_what_where(self, session: Session) -> None:
        document = make_document(session)
        add_proposal(session, document)
        app_settings = session.get(AppSettings, 1)
        assert app_settings is not None

        router_service.approve(
            session,
            FakeWebDav(),  # type: ignore[arg-type]
            app_settings,
            document.id,
            final_name="Renamed",
            final_folder_path=ROOT,
            document_date=date(2026, 8, 21),
            actor=ACTOR,
        )

        (event,) = events(session)
        assert event.action == AuditAction.document_approved
        assert event.actor_label == "owner@example.com"
        assert event.actor_user_id == "user-1"
        assert event.document_id == document.id
        assert event.details["to"] == f"{ROOT}/Renamed.pdf"
        assert event.details["was_overridden"] is True

    def test_a_failed_approve_records_nothing(self, session: Session) -> None:
        """No entry claiming a filing that did not happen."""
        document = make_document(session)
        app_settings = session.get(AppSettings, 1)
        assert app_settings is not None
        webdav = FakeWebDav()
        webdav.existing.add(f"{ROOT}/Taken.pdf")

        with pytest.raises(FilenameCollision):
            router_service.approve(
                session,
                webdav,  # type: ignore[arg-type]
                app_settings,
                document.id,
                final_name="Taken",
                final_folder_path=ROOT,
                document_date=None,
                actor=ACTOR,
            )

        session.rollback()
        assert events(session) == []


class TestThroughTheApi:
    def test_a_settings_save_lists_only_what_changed(self, client: TestClient) -> None:
        payload = {"allowed_root_folders": ["/Documents"], "trash_folder_path": "/Trash"}
        client.put("/api/v1/settings", json=payload)
        client.put("/api/v1/settings", json={**payload, "filename_pattern_hint": "YYYY.MM.DD"})

        first, second = db_events(AuditAction.settings_updated)
        assert set(first.details["changed"]) == {"allowed_root_folders", "trash_folder_path"}  # type: ignore[arg-type]
        assert second.details["changed"] == {
            "filename_pattern_hint": {"from": None, "to": "YYYY.MM.DD"}
        }
        assert second.summary == "Changed pattern hint"
        assert second.actor_label == TEST_EMAIL

    def test_an_api_key_is_recorded_as_changed_and_never_as_itself(
        self, client: TestClient
    ) -> None:
        key = "sk-canary-must-not-be-audited"
        created = client.post(
            "/api/v1/ai/endpoints",
            json={"name": "Hosted", "base_url": "https://api.example.com/v1", "api_key": key},
        ).json()
        client.put(
            f"/api/v1/ai/endpoints/{created['id']}",
            json={"name": "Hosted", "base_url": "https://api.example.com/v2", "api_key": key},
        )

        (updated,) = db_events(AuditAction.ai_endpoint_updated)
        assert updated.details["api_key_changed"] is True
        assert updated.details["changed"] == {
            "base_url": {"from": "https://api.example.com/v1", "to": "https://api.example.com/v2"}
        }
        everything = json.dumps(
            [[e.summary, e.details, e.actor_label] for e in db_events()], default=str
        )
        assert key not in everything

    def test_sign_ins_are_recorded_and_a_failure_never_keeps_the_password(
        self, client: TestClient
    ) -> None:
        client.post("/api/v1/auth/logout")
        client.post("/api/v1/auth/login", json={"email": TEST_EMAIL, "password": "wrong-password!"})

        assert [e.action for e in db_events()] == [
            AuditAction.auth_registered,
            AuditAction.auth_logout,
            AuditAction.auth_login_failed,
        ]
        (failed,) = db_events(AuditAction.auth_login_failed)
        assert failed.actor_label == TEST_EMAIL
        assert failed.actor_user_id is None
        assert "wrong-password!" not in json.dumps(failed.details)

    def test_only_the_admin_can_read_it(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert client.get("/api/v1/audit").status_code == 200

        monkeypatch.setattr(config, "allow_registration", True)
        client.post("/api/v1/auth/logout")
        client.post(
            "/api/v1/auth/register",
            json={"email": "second@example.com", "password": "another-long-password"},
        )

        response = client.get("/api/v1/audit")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "admin_required"
        assert client.get("/api/v1/audit/usage").status_code == 403

    def test_pages_newest_first_and_filters_by_category(self, client: TestClient) -> None:
        for hint in ("a", "b", "c"):
            client.put(
                "/api/v1/settings",
                json={
                    "allowed_root_folders": ["/Documents"],
                    "trash_folder_path": "/Trash",
                    "filename_pattern_hint": hint,
                },
            )

        first = client.get("/api/v1/audit", params={"limit": 2, "category": "settings"}).json()
        second = client.get(
            "/api/v1/audit",
            params={"limit": 2, "category": "settings", "cursor": first["next_cursor"]},
        ).json()

        hints = [
            item["details"]["changed"]["filename_pattern_hint"]["to"]
            for item in first["items"] + second["items"]
        ]
        assert hints == ["c", "b", "a"]
        assert second["next_cursor"] is None
        # The registration is there, just not under "settings".
        auth = client.get("/api/v1/audit", params={"category": "auth"}).json()["items"]
        assert [item["action"] for item in auth] == ["auth.registered"]

    def test_rejects_an_unknown_category(self, client: TestClient) -> None:
        assert client.get("/api/v1/audit", params={"category": "nope"}).status_code == 422


def _summary(extraction: tuple[int, int] | None, filing: tuple[int, int]) -> ai.UsageSummary:
    meter = ai.UsageMeter()
    if extraction:
        meter.scope("extraction", "Local · vl").record(ai.TokenUsage(*extraction), 1)
    meter.scope("filing", "Hosted · gpt").record(ai.TokenUsage(*filing), 1)
    summary = meter.summary()
    assert summary is not None
    return summary


class TestUsageTotals:
    def test_adds_up_analyses_and_comparisons_per_model(self, session: Session) -> None:
        analysis = _summary((1000, 200), (800, 50))
        audit.record(
            session,
            AuditAction.document_analyzed,
            actor=Actor.system(),
            summary="Proposed",
            details=audit.usage_details(analysis),
            usage=[analysis],
        )
        method = _summary(None, (700, 40))
        audit.record(
            session,
            AuditAction.document_compared,
            actor=ACTOR,
            summary="Compared",
            details={"methods": [{"method": "ocr", **audit.usage_details(method)}]},
            usage=[method],
        )
        session.commit()

        totals = audit.usage_totals(session, days=30)

        assert (totals.analyses, totals.comparisons) == (1, 1)
        assert totals.total_tokens == 1000 + 200 + 800 + 50 + 700 + 40
        filing = next(m for m in totals.by_model if m.task == "filing")
        assert (filing.model_label, filing.prompt_tokens, filing.calls) == ("Hosted · gpt", 1500, 2)

    def test_leaves_out_what_is_older_than_the_window(self, session: Session) -> None:
        old = audit.record(
            session,
            AuditAction.document_analyzed,
            actor=Actor.system(),
            summary="Proposed",
            usage=[_summary(None, (500, 5))],
        )
        old.occurred_at = to_storage(utc_now() - timedelta(days=40))
        session.commit()

        assert audit.usage_totals(session, days=30).total_tokens == 0


class TestPrune:
    def test_drops_only_what_is_past_retention(self, session: Session) -> None:
        old = audit.record(session, AuditAction.auth_login, actor=ACTOR, summary="old")
        old.occurred_at = to_storage(utc_now() - timedelta(days=400))
        audit.record(session, AuditAction.auth_login, actor=ACTOR, summary="recent")
        session.commit()

        assert audit.prune(session, 365) == 1
        assert [event.summary for event in events(session)] == ["recent"]

    def test_zero_keeps_everything(self, session: Session) -> None:
        old = audit.record(session, AuditAction.auth_login, actor=ACTOR, summary="old")
        old.occurred_at = to_storage(utc_now() - timedelta(days=4000))
        session.commit()

        assert audit.prune(session, 0) == 0
        assert len(events(session)) == 1
