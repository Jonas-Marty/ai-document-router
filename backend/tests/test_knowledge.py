"""Archive knowledge -- SPEC 6.3c: normalising, learning on approve, and what a prompt gets."""
# ruff: noqa: F811 -- the router tests' fixtures are imported and then requested by name

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import db
from app.models import (
    AppSettings,
    AuditEvent,
    Document,
    DocumentStatus,
    HistoryAction,
    HistoryEntry,
    KnownAccount,
    KnownSender,
    Reading,
)
from app.services import knowledge, naming
from app.services import router as router_service
from app.services.errors import ValidationError, WebDAVUnreachable
from app.services.times import utc_now
from tests.test_documents_api import webdav_override  # noqa: F401
from tests.test_router import (
    OWNER,
    ROOT,
    TEST_ACTOR,
    FakeWebDav,
    app_settings,  # noqa: F401 -- fixtures, used by name
    make_document,
    session,  # noqa: F401
    webdav,  # noqa: F401
)


class TestNormalising:
    @pytest.mark.parametrize(
        ("left", "right"),
        [
            ("Zürcher Kantonalbank", "ZUERCHER-KANTONALBANK"),
            ("  WIR Bank ", "wir bank"),
            ("738.953-48.5800", "738 953 48 5800"),
            ("Gönnerschaft", "Goennerschaft"),
        ],
    )
    def test_the_same_after_normalising(self, left: str, right: str) -> None:
        assert knowledge.normalise(left) == knowledge.normalise(right)

    def test_an_accounts_key_is_its_longest_digit_run(self) -> None:
        assert knowledge.account_key("frankly 3a CH33 0070 0114 8034 4224 8") == (
            "3300700114803442248"
        )
        assert knowledge.account_key("738.953-48.5800") == "738953485800"

    def test_a_short_number_keys_on_the_whole_value(self) -> None:
        assert knowledge.account_key("Police A-12") == "policea12"


class TestTemplate:
    def test_assembles_in_the_templates_order_and_leaves_out_what_it_omits(self) -> None:
        fields = naming.NameFields(sender="ZKB", title="Abrechnung", account="CH33", number="7")
        name = naming.assemble_name(
            date(2026, 1, 9), fields, ["sender", "date", "title", "account"]
        )

        assert name == "ZKB 2026.01.09 Abrechnung CH33"

    @pytest.mark.parametrize(
        ("template", "message"),
        [
            (["date", "sender"], "must include title"),
            (["date", "sender", "title", "title"], "more than once"),
            (["date", "sender", "title", "colour"], "unknown field"),
        ],
    )
    def test_rejects_a_bad_template(self, template: list[str], message: str) -> None:
        with pytest.raises(ValidationError, match=message):
            knowledge.validate_template(template)


def approve(
    session: Session,
    webdav: FakeWebDav,
    settings: AppSettings,
    fields: dict[str, str],
    *,
    name: str = "scan.pdf",
    sender: bool = False,
    account: bool = False,
) -> Document:
    document = make_document(session, name)
    router_service.approve(
        session,
        webdav,  # type: ignore[arg-type]
        settings,
        document.id,
        final_name=None,
        final_folder_path=ROOT,
        document_date=date(2026, 5, 13),
        name_fields=fields,
        remember_sender=sender,
        remember_account=account,
        actor=TEST_ACTOR,
    )
    return document


WIR = {"sender": "WIR Bank", "account": "738.953-48.5800", "title": "Konto"}


class TestRememberOnApprove:
    def test_learns_nothing_unless_asked(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        approve(session, webdav, app_settings, WIR)

        assert session.exec(select(KnownSender)).all() == []

    def test_remembering_the_account_remembers_its_sender(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        approve(session, webdav, app_settings, WIR, account=True)

        [sender] = session.exec(select(KnownSender)).all()
        [account] = session.exec(select(KnownAccount)).all()
        assert (sender.user_id, sender.short_name) == (OWNER, "WIR Bank")
        assert (account.sender_id, account.value) == (sender.id, "738.953-48.5800")
        logged = session.exec(select(AuditEvent).where(AuditEvent.action == "knowledge.learned"))
        assert "WIR Bank" in logged.one().summary

    def test_what_is_known_is_not_added_twice(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        approve(session, webdav, app_settings, WIR, sender=True, account=True)
        again = {**WIR, "sender": "wir bank", "account": "738 953 48 5800"}
        approve(session, webdav, app_settings, again, name="b.pdf", sender=True, account=True)

        assert len(session.exec(select(KnownSender)).all()) == 1
        assert len(session.exec(select(KnownAccount)).all()) == 1

    def test_a_failed_move_learns_nothing(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        webdav.fail_move = WebDAVUnreachable("down")

        with pytest.raises(WebDAVUnreachable):
            approve(session, webdav, app_settings, WIR, sender=True, account=True)

        session.rollback()
        assert session.exec(select(KnownSender)).all() == []

    def test_files_under_the_owners_template(
        self, session: Session, webdav: FakeWebDav, app_settings: AppSettings
    ) -> None:
        app_settings.name_template = ["sender", "title", "date"]
        session.add(app_settings)
        session.commit()

        approve(session, webdav, app_settings, WIR)

        assert webdav.moves[0][1] == f"{ROOT}/WIR Bank Konto 2026.05.13.pdf"


def add_sender(session: Session, name: str, aliases: list[str], *accounts: str) -> KnownSender:
    sender = KnownSender(user_id=OWNER, short_name=name, aliases=aliases, created_at=utc_now())
    session.add(sender)
    session.flush()
    for value in accounts:
        session.add(
            KnownAccount(user_id=OWNER, sender_id=sender.id, value=value, created_at=utc_now())
        )
    session.commit()
    return sender


def filed_before(
    session: Session, name: str, text: str, fields: dict[str, str | None], **entry: object
) -> HistoryEntry:
    document = make_document(session, name)
    reading = Reading(
        user_id=OWNER,
        document_id=document.id,
        setup_key="k",
        method="text_layer",
        label="Text layer",
        text=text,
        created_at=utc_now(),
    )
    session.add(reading)
    session.flush()
    document.reading_id = reading.id
    document.status = DocumentStatus.moved
    history = HistoryEntry(
        document_id=document.id,
        original_filename=name,
        final_filename=f"{name}.pdf",
        final_folder_path=f"{ROOT}/{fields.get('sender')}",
        source_folder_path="/Test-Inbox",
        action=HistoryAction.moved,
        was_overridden=False,
        suggestion_snapshot={},
        name_fields=fields,
        processed_at=entry.pop("processed_at", datetime(2026, 1, 1)),  # type: ignore[arg-type]
        revertible=entry.pop("revertible", True),  # type: ignore[arg-type]
    )
    session.add_all([document, history])
    session.commit()
    return history


class TestForPrompt:
    def test_offers_the_senders_found_in_the_text_with_how_they_are_written(
        self, session: Session, app_settings: AppSettings
    ) -> None:
        add_sender(session, "ZKB", ["Zürcher Kantonalbank"])
        add_sender(session, "Rega", [])

        block = knowledge.for_prompt(
            session, app_settings, "ZUERCHER KANTONALBANK, Postfach", 0
        ).text

        assert "Known senders — use the short name exactly:" in block
        assert "ZKB (written as: Zürcher Kantonalbank)" in block
        assert "Rega" not in block

    def test_offers_every_short_name_when_none_is_found(
        self, session: Session, app_settings: AppSettings
    ) -> None:
        add_sender(session, "ZKB", [])
        add_sender(session, "Rega", [])

        block = knowledge.for_prompt(session, app_settings, "Swisscom invoice", 0).text

        assert "Senders seen before" in block
        assert "  Rega\n  ZKB" in block

    def test_gives_only_the_accounts_in_the_text(
        self, session: Session, app_settings: AppSettings
    ) -> None:
        add_sender(session, "ZKB", [], "frankly 3a CH33 0070 0114 8034 4224 8", "CH99 1111 2222")

        block = knowledge.for_prompt(
            session, app_settings, "IBAN: CH33 0070 0114 8034 4224 8", 0
        ).text

        assert "ZKB: frankly 3a CH33 0070 0114 8034 4224 8" in block
        assert "CH99" not in block

    def test_examples_put_the_same_sender_first_and_skip_reverted_ones(
        self, session: Session, app_settings: AppSettings
    ) -> None:
        add_sender(session, "Rega", [])
        filed_before(
            session,
            "new",
            "Swisscom Rechnung",
            {"sender": "Swisscom", "title": "Rechnung"},
            processed_at=datetime(2026, 3, 1),
        )
        filed_before(session, "old", "Rega Gönner", {"sender": "Rega", "title": "Gönnerschaft"})
        filed_before(
            session,
            "undone",
            "Rega",
            {"sender": "Rega", "title": "Reverted"},
            revertible=False,
        )

        archive = knowledge.for_prompt(session, app_settings, "Rega Gönnerbeitrag", 8)

        assert archive.has_examples
        text = archive.text
        assert text.index("title Gönnerschaft") < text.index("title Rechnung")
        assert "Reverted" not in text
        assert f"Folder: {ROOT}/Rega" in text

    def test_holds_the_filing_rules(self, session: Session, app_settings: AppSettings) -> None:
        app_settings.filing_rules = "Insurance goes under /Versicherung."

        archive = knowledge.for_prompt(session, app_settings, "text", 8)

        assert archive.text == "Filing rules for this archive:\nInsurance goes under /Versicherung."
        assert not archive.has_examples


class TestApi:
    def test_reads_the_default_and_saves_a_template_and_rules(self, client: TestClient) -> None:
        assert client.get("/api/v1/knowledge").json() == {
            "name_template": ["date", "sender", "account", "title", "period", "number"],
            "filing_rules": None,
        }

        saved = client.put(
            "/api/v1/knowledge",
            json={"name_template": ["sender", "date", "title"], "filing_rules": "  Be tidy.  "},
        )

        assert saved.json() == {
            "name_template": ["sender", "date", "title"],
            "filing_rules": "Be tidy.",
        }

    def test_refuses_a_template_without_the_date(self, client: TestClient) -> None:
        response = client.put(
            "/api/v1/knowledge", json={"name_template": ["sender", "title"], "filing_rules": None}
        )

        assert response.status_code == 422
        assert "must include date" in response.json()["error"]["message"]

    def test_edits_and_forgets_senders_and_accounts(self, client: TestClient) -> None:
        with Session(db.engine) as session:
            sender = KnownSender(
                user_id=_owner(), short_name="ZKB", aliases=[], created_at=utc_now()
            )
            session.add(sender)
            session.flush()
            account = KnownAccount(
                user_id=sender.user_id, sender_id=sender.id, value="CH33", created_at=utc_now()
            )
            session.add(account)
            session.commit()
            sender_id, account_id = sender.id, account.id

        edited = client.put(
            f"/api/v1/knowledge/senders/{sender_id}",
            json={
                "short_name": " ZKB ",
                "aliases": ["Zürcher Kantonalbank", " ", "ZÜRCHER KANTONALBANK"],
            },
        ).json()
        assert edited["aliases"] == ["Zürcher Kantonalbank"]
        assert edited["accounts"] == [{"id": account_id, "value": "CH33"}]

        assert client.delete(f"/api/v1/knowledge/accounts/{account_id}").status_code == 204
        assert client.get("/api/v1/knowledge/senders").json()[0]["accounts"] == []
        assert client.delete(f"/api/v1/knowledge/senders/{sender_id}").status_code == 204
        assert client.get("/api/v1/knowledge/senders").json() == []

    def test_someone_elses_sender_is_a_404(self, client: TestClient) -> None:
        with Session(db.engine) as session:
            theirs = KnownSender(
                user_id="someone-else", short_name="Theirs", aliases=[], created_at=utc_now()
            )
            session.add(theirs)
            session.commit()
            theirs_id = theirs.id

        body = {"short_name": "Mine", "aliases": []}
        assert client.put(f"/api/v1/knowledge/senders/{theirs_id}", json=body).status_code == 404
        assert client.delete(f"/api/v1/knowledge/senders/{theirs_id}").status_code == 404
        assert client.get("/api/v1/knowledge/senders").json() == []

    def test_a_rename_cannot_take_another_senders_name(self, client: TestClient) -> None:
        with Session(db.engine) as session:
            for name in ("ZKB", "Rega"):
                session.add(
                    KnownSender(user_id=_owner(), short_name=name, aliases=[], created_at=utc_now())
                )
            session.commit()
        rega = next(
            s for s in client.get("/api/v1/knowledge/senders").json() if s["short_name"] == "Rega"
        )

        response = client.put(
            f"/api/v1/knowledge/senders/{rega['id']}", json={"short_name": "zkb", "aliases": []}
        )

        assert response.status_code == 422
        assert "already a known sender" in response.json()["error"]["message"]

    def test_approve_remembers_through_the_api(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        webdav_override: object,
    ) -> None:
        captured: dict[str, object] = {}

        def fake_approve(*args: object, **kwargs: object) -> None:
            captured.update(kwargs)
            raise ValidationError("stop here")

        monkeypatch.setattr(router_service, "approve", fake_approve)
        with Session(db.engine) as session:
            document = Document(
                user_id=_owner(),
                webdav_path="/Inbox/a.pdf",
                original_filename="a.pdf",
                mime_type="application/pdf",
                file_size_bytes=1,
                content_hash="h",
                scanned_at=utc_now(),
                discovered_at=utc_now(),
            )
            session.add(document)
            session.commit()
            document_id = document.id

        response = client.post(
            f"/api/v1/documents/{document_id}/approve",
            json={
                "name_fields": {"sender": "WIR Bank", "title": "Konto"},
                "final_folder_path": "/Archive",
                "document_date": "2026-05-13",
                "remember_sender": True,
            },
        )

        assert response.json()["error"]["message"] == "stop here", response.text
        assert (captured["remember_sender"], captured["remember_account"]) == (True, False)


def _owner() -> str:
    from tests.conftest import signed_in_user_id

    return signed_in_user_id()
