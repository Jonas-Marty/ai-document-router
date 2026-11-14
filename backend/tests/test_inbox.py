"""The Inbox: groups, search, flags, thumbnails, bulk actions, estimate -- SPEC 6.4b."""

# ruff: noqa: F811 - pytest fixtures are imported by name and shadowed by parameters

import io
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session

from app import db
from app.config import settings as config
from app.deps import get_optional_webdav, get_webdav
from app.main import app
from app.models import (
    AiCall,
    AiConfiguration,
    AiEndpoint,
    AppSettings,
    Document,
    DocumentStatus,
    HistoryAction,
    HistoryEntry,
    KnownAccount,
    KnownSender,
    Proposal,
    ProposalStatus,
    Reading,
    ReadingMethod,
    User,
)
from app.services import ai, configurations, connections, inbox, progress
from app.services.errors import NotFoundError, WebDAVUnreachable
from app.services.times import utc_now
from app.services.webdav import WebDavEntry
from tests.conftest import signed_in_user_id
from tests.test_router import OWNER, ROOT, TEST_ACTOR, app_settings, session  # noqa: F401


def pdf_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (595, 842), "white").save(buffer, format="PDF")
    return buffer.getvalue()


class FakeWebDav:
    def __init__(self) -> None:
        self.listings: dict[str, list[str]] = {}
        self.list_error: Exception | None = None
        self.reads = 0
        self.body = pdf_bytes()
        self.existing: set[str] = set()
        self.moves: list[tuple[str, str]] = []
        self.fail_for: set[str] = set()

    def list_dir(self, path: str) -> list[WebDavEntry]:
        if self.list_error is not None:
            raise self.list_error
        return [
            WebDavEntry(path=f"{path}/{name}", name=name, is_dir=False)
            for name in self.listings.get(path, [])
        ]

    def read_stream(self, path: str, chunk_size: int = 65536):  # type: ignore[no-untyped-def]
        self.reads += 1
        yield self.body

    def exists(self, path: str) -> bool:
        return path in self.existing

    def mkdir_p(self, path: str) -> None:
        self.existing.add(path)

    def move(self, source: str, destination: str) -> None:
        if any(name in source for name in self.fail_for):
            raise WebDAVUnreachable("The Nextcloud didn't answer.")
        self.moves.append((source, destination))
        self.existing.add(destination)


_counter = iter(range(10_000))


def document(
    session: Session,
    name: str,
    *,
    status: DocumentStatus = DocumentStatus.pending,
    proposal_status: ProposalStatus = ProposalStatus.ready,
    scanned_at: datetime | None = None,
    user_id: str = OWNER,
    mime_type: str = "application/pdf",
) -> Document:
    found = Document(
        user_id=user_id,
        webdav_path=f"/Test-Inbox/{name}",
        original_filename=name,
        mime_type=mime_type,
        file_size_bytes=1024,
        page_count=1,
        content_hash=f"hash-{name}-{next(_counter)}",
        scanned_at=scanned_at or datetime(2026, 8, 21, 9, 0),
        discovered_at=datetime(2026, 8, 21, 9, 1),
        status=status,
        proposal_status=proposal_status,
    )
    session.add(found)
    session.commit()
    return found


def propose(
    session: Session,
    target: Document,
    *,
    sender: str | None = "WIR Bank",
    title: str | None = "Kontoauszug",
    account: str | None = None,
    folder: str = f"{ROOT}/Bank/2026",
    document_date: date | None = date(2026, 5, 13),
    free_name: bool = False,
) -> Proposal:
    proposal = Proposal(
        document_id=target.id,
        suggested_name=f"2026.05.13 {sender or ''} {title or ''}".strip(),
        target_folder_path=folder,
        document_date=document_date,
        confidence_score=0.9,
        reasoning_text="",
        model_name="m",
        created_at=utc_now(),
        sender=None if free_name else sender,
        title=None if free_name else title,
        account=None if free_name else account,
        configuration_name="Daily",
    )
    session.add(proposal)
    session.commit()
    return proposal


def file_history(
    session: Session,
    target: Document,
    folder: str,
    *,
    sender: str,
    account: str | None = None,
    action: HistoryAction = HistoryAction.moved,
    revertible: bool = True,
) -> HistoryEntry:
    entry = HistoryEntry(
        document_id=target.id,
        original_filename=target.original_filename,
        final_filename=f"{sender}.pdf",
        final_folder_path=folder,
        source_folder_path="/Test-Inbox",
        action=action,
        was_overridden=False,
        suggestion_snapshot={},
        name_fields={"sender": sender, "title": "x", "account": account},
        processed_at=utc_now(),
        revertible=revertible,
    )
    session.add(entry)
    session.commit()
    return entry


def know(session: Session, short_name: str, *accounts: str, aliases: list[str] | None = None):  # type: ignore[no-untyped-def]
    sender = KnownSender(
        user_id=OWNER, short_name=short_name, aliases=aliases or [], created_at=utc_now()
    )
    session.add(sender)
    session.commit()
    for value in accounts:
        session.add(
            KnownAccount(user_id=OWNER, sender_id=sender.id, value=value, created_at=utc_now())
        )
    session.commit()
    return sender


def ready_page(session: Session, webdav: FakeWebDav | None = None, **kwargs):  # type: ignore[no-untyped-def]
    return inbox.page(session, webdav, OWNER, group=inbox.Group.ready, **kwargs)  # type: ignore[arg-type]


class TestGroups:
    def test_each_document_is_in_exactly_one_group(self, session: Session) -> None:
        document(session, "waiting.pdf", proposal_status=ProposalStatus.pending)
        document(session, "failed.pdf", proposal_status=ProposalStatus.failed)
        document(session, "ready.pdf")
        document(session, "later.pdf", status=DocumentStatus.skipped)
        document(session, "filed.pdf", status=DocumentStatus.moved)
        document(session, "binned.pdf", status=DocumentStatus.trashed)
        document(session, "theirs.pdf", user_id="someone-else")

        assert inbox.counts(session, OWNER) == {"analyse": 2, "ready": 2, "done": 1, "trashed": 1}
        later = [card for card in ready_page(session).items if card.later]
        assert [card.original_filename for card in later] == ["later.pdf"]

    def test_analyse_cards_say_why_they_wait_and_hide_a_stale_proposal(
        self, session: Session
    ) -> None:
        waiting = document(session, "waiting.pdf", proposal_status=ProposalStatus.pending)
        waiting.waiting_reason = "Waiting for Tower"
        propose(session, waiting)
        failed = document(session, "failed.pdf", proposal_status=ProposalStatus.failed)
        failed.proposal_error = "No text layer"
        session.commit()

        cards = {
            card.original_filename: card
            for card in inbox.page(session, None, OWNER, group=inbox.Group.analyse).items
        }
        assert cards["waiting.pdf"].waiting_reason == "Waiting for Tower"
        assert cards["waiting.pdf"].name is None
        assert cards["failed.pdf"].error == "No text layer"

    def test_documents_being_analysed_come_first_and_are_counted(self, session: Session) -> None:
        old = document(
            session,
            "old.pdf",
            proposal_status=ProposalStatus.pending,
            scanned_at=datetime(2026, 1, 1),
        )
        document(
            session,
            "new.pdf",
            proposal_status=ProposalStatus.pending,
            scanned_at=datetime(2026, 3, 1),
        )
        with progress.tracking(old.id):
            progress.report("Reading page 2 of 3")
            result = inbox.page(session, None, OWNER, group=inbox.Group.analyse)

        assert [card.original_filename for card in result.items] == ["old.pdf", "new.pdf"]
        assert result.items[0].analysis_stage == "Reading page 2 of 3"
        assert result.analysing == 1

    def test_done_cards_carry_where_the_file_went_and_whether_it_can_go_back(
        self, session: Session
    ) -> None:
        filed = document(session, "filed.pdf", status=DocumentStatus.moved)
        propose(session, filed)
        entry = file_history(session, filed, f"{ROOT}/Bank/2026", sender="WIR Bank")

        (card,) = inbox.page(session, None, OWNER, group=inbox.Group.done).items
        assert card.name == "WIR Bank.pdf"
        assert card.folder == f"{ROOT}/Bank/2026"
        assert card.history_entry_id == entry.id
        assert card.revertible is True
        assert card.sender == "WIR Bank"


class TestSearchFilterSort:
    def test_search_reaches_names_and_the_reading_text(self, session: Session) -> None:
        by_name = document(session, "Rechnung-Swisscom.pdf")
        propose(session, by_name, sender="Swisscom")
        by_text = document(session, "scan-002.pdf")
        propose(session, by_text, sender="Rega")
        reading = Reading(
            user_id=OWNER,
            document_id=by_text.id,
            setup_key="k",
            method=ReadingMethod.text_layer,
            label="Text layer",
            text="Gönnerbeitrag 2026 SWISSCOM erwähnt",
            created_at=utc_now(),
        )
        session.add(reading)
        session.commit()
        by_text.reading_id = reading.id
        session.commit()
        document(session, "unrelated.pdf")

        found = ready_page(session, query="swisscom").items
        assert {card.original_filename for card in found} == {
            "Rechnung-Swisscom.pdf",
            "scan-002.pdf",
        }

    def test_a_percent_sign_is_searched_for_not_a_wildcard(self, session: Session) -> None:
        document(session, "100% sure.pdf")
        document(session, "other.pdf")

        assert [card.original_filename for card in ready_page(session, query="%").items] == [
            "100% sure.pdf"
        ]

    def test_filters_narrow_the_cards_but_not_the_options(self, session: Session) -> None:
        propose(session, document(session, "a.pdf"), sender="Rega", folder=f"{ROOT}/Rega")
        propose(session, document(session, "b.pdf"), sender="ZKB", folder=f"{ROOT}/Bank")

        result = ready_page(session, sender="ZKB")
        assert [card.original_filename for card in result.items] == ["b.pdf"]
        assert result.senders == ["Rega", "ZKB"]
        assert result.folders == [f"{ROOT}/Bank", f"{ROOT}/Rega"]
        assert result.total == 1

    def test_sorts(self, session: Session) -> None:
        old = document(session, "old.pdf", scanned_at=datetime(2026, 1, 1))
        propose(session, old, sender="Zebra", document_date=date(2026, 9, 1))
        new = document(session, "new.pdf", scanned_at=datetime(2026, 9, 1))
        propose(session, new, sender="Alpha", document_date=None)
        mid = document(session, "mid.pdf", scanned_at=datetime(2026, 5, 1))
        propose(session, mid, sender=None, document_date=date(2026, 1, 1))

        def order(sort: inbox.Sort) -> list[str]:
            return [card.original_filename for card in ready_page(session, sort=sort).items]

        assert order(inbox.Sort.newest) == ["new.pdf", "mid.pdf", "old.pdf"]
        assert order(inbox.Sort.document_date) == ["old.pdf", "mid.pdf", "new.pdf"]
        assert order(inbox.Sort.sender) == ["new.pdf", "old.pdf", "mid.pdf"]
        # new is a new sender without a date; the others have one flag each.
        assert order(inbox.Sort.needs_check) == ["new.pdf", "mid.pdf", "old.pdf"]

    def test_paging(self, session: Session) -> None:
        for day in range(1, 6):
            document(session, f"{day}.pdf", scanned_at=datetime(2026, 8, day))

        result = ready_page(session, offset=2, limit=2)
        assert [card.original_filename for card in result.items] == ["3.pdf", "2.pdf"]
        assert result.total == 5


class TestNeedsACheck:
    def flags(self, session: Session, **proposal: object) -> list[str]:
        target = document(session, f"doc-{next(_counter)}.pdf")
        propose(session, target, **proposal)  # type: ignore[arg-type]
        (card,) = [card for card in ready_page(session).items if card.id == target.id]
        return card.flags

    def test_missing_date(self, session: Session) -> None:
        # A proposal without a sender or title is refused when it arrives (ai._fields), so
        # among proposals with fields only the date can be missing.
        know(session, "WIR Bank")
        assert self.flags(session, document_date=None) == ["missing_date"]

    def test_a_free_name_is_only_checked_for_its_date(self, session: Session) -> None:
        assert self.flags(session, free_name=True) == []
        assert self.flags(session, free_name=True, document_date=None) == ["missing_date"]

    def test_new_sender_unless_known_by_name_or_alias(self, session: Session) -> None:
        assert self.flags(session, sender="Rega") == ["new_sender"]
        know(session, "ZKB", aliases=["Zürcher Kantonalbank"])
        assert self.flags(session, sender="ZUERCHER KANTONALBANK") == []

    def test_new_and_near_match_accounts(self, session: Session) -> None:
        know(session, "WIR Bank", "738.953-48.5800")
        assert self.flags(session, account="738 953 48 5800") == []
        assert self.flags(session, account="739.953-48.5800") == ["near_match_account"]
        assert self.flags(session, account="111.222-33.4444") == ["new_account"]
        # An account on an unknown sender is part of the new sender, not flagged twice.
        assert self.flags(session, sender="Rega", account="12345678") == ["new_sender"]

    def test_unusual_folder_learns_from_approvals_with_years_generalised(
        self, session: Session
    ) -> None:
        know(session, "WIR Bank")
        filed = document(session, "filed.pdf", status=DocumentStatus.moved)
        file_history(session, filed, f"{ROOT}/Finance/WIR Bank/2025", sender="WIR Bank")

        assert self.flags(session, folder=f"{ROOT}/Finance/WIR Bank/2026") == []
        assert self.flags(session, folder=f"{ROOT}/Inbox/2026") == ["unusual_folder"]

    def test_a_sender_may_have_several_usual_folders(self, session: Session) -> None:
        know(session, "WIR Bank")
        for folder in (f"{ROOT}/Finance/WIR Bank/2025", f"{ROOT}/Inbox/2024"):
            filed = document(session, f"f{next(_counter)}.pdf", status=DocumentStatus.moved)
            file_history(session, filed, folder, sender="WIR Bank")

        assert self.flags(session, folder=f"{ROOT}/Inbox/2026") == []

    def test_an_account_with_history_is_held_to_its_own_folders(self, session: Session) -> None:
        know(session, "WIR Bank", "738.953-48.5800", "111.222-33.4444")
        one = document(session, "one.pdf", status=DocumentStatus.moved)
        file_history(session, one, f"{ROOT}/Konto A", sender="WIR Bank", account="738.953-48.5800")
        two = document(session, "two.pdf", status=DocumentStatus.moved)
        file_history(session, two, f"{ROOT}/Konto B", sender="WIR Bank", account="111.222-33.4444")

        assert self.flags(session, account="738.953-48.5800", folder=f"{ROOT}/Konto B") == [
            "unusual_folder"
        ]
        # An account never filed before falls back to all of the sender's folders.
        assert self.flags(session, account="555.666-77.8888", folder=f"{ROOT}/Konto B") == [
            "new_account"
        ]

    def test_reverted_approvals_and_brand_new_senders_teach_nothing(self, session: Session) -> None:
        filed = document(session, "filed.pdf", status=DocumentStatus.moved)
        file_history(session, filed, f"{ROOT}/Wrong", sender="Rega", revertible=False)

        assert self.flags(session, sender="Rega", folder=f"{ROOT}/Right") == ["new_sender"]

    def test_name_taken_on_the_page_only_and_quietly_without_a_server(
        self, session: Session
    ) -> None:
        know(session, "WIR Bank")
        target = document(session, "doc.pdf")
        propose(session, target)
        webdav = FakeWebDav()
        webdav.listings[f"{ROOT}/Bank/2026"] = ["2026.05.13 WIR Bank Kontoauszug.pdf"]

        assert ready_page(session, webdav).items[0].flags == ["name_taken"]
        webdav.list_error = WebDAVUnreachable("down")
        assert ready_page(session, webdav).items[0].flags == []
        assert ready_page(session, None).items[0].flags == []


class TestThumbnail:
    def test_renders_the_first_page_once(self, session: Session) -> None:
        target = document(session, "scan.pdf")
        webdav = FakeWebDav()

        first = inbox.thumbnail(session, webdav, OWNER, target.id)  # type: ignore[arg-type]
        again = inbox.thumbnail(session, None, OWNER, target.id)

        assert first == again
        assert webdav.reads == 1
        with Image.open(io.BytesIO(first)) as image:
            assert image.format == "JPEG"
            assert image.width <= inbox.THUMBNAIL_WIDTH

    def test_scales_an_image_and_refuses_other_files(self, session: Session) -> None:
        photo = document(session, "photo.png", mime_type="image/png")
        webdav = FakeWebDav()
        buffer = io.BytesIO()
        Image.new("RGB", (2000, 1000), "red").save(buffer, format="PNG")
        webdav.body = buffer.getvalue()
        with Image.open(io.BytesIO(inbox.thumbnail(session, webdav, OWNER, photo.id))) as image:  # type: ignore[arg-type]
            assert image.size == (inbox.THUMBNAIL_WIDTH, 160)

        text = document(session, "notes.txt", mime_type="text/plain")
        with pytest.raises(NotFoundError):
            inbox.thumbnail(session, webdav, OWNER, text.id)  # type: ignore[arg-type]

    def test_a_broken_file_and_someone_elses_are_not_found(self, session: Session) -> None:
        broken = document(session, "broken.pdf")
        webdav = FakeWebDav()
        webdav.body = b"not a pdf"
        with pytest.raises(NotFoundError):
            inbox.thumbnail(session, webdav, OWNER, broken.id)  # type: ignore[arg-type]
        theirs = document(session, "theirs.pdf", user_id="someone-else")
        with pytest.raises(NotFoundError):
            inbox.thumbnail(session, FakeWebDav(), OWNER, theirs.id)  # type: ignore[arg-type]


def add_configuration(session: Session, *, vision: bool = False) -> AiConfiguration:
    endpoint = AiEndpoint(
        user_id=OWNER,
        name="Infomaniak",
        base_url="https://h.test/v1",
        created_at=utc_now(),
        model_prices=[
            {"model_name": "big", "price_input_per_million": 1.0, "price_output_per_million": 4.0}
        ],
    )
    session.add(endpoint)
    session.commit()
    configuration = AiConfiguration(
        user_id=OWNER,
        name="Daily",
        is_default=True,
        reading_method=ReadingMethod.vision if vision else ReadingMethod.text_layer,
        reading_endpoint_id=endpoint.id if vision else None,
        reading_model_name="eyes" if vision else None,
        filing_endpoint_id=endpoint.id,
        filing_model_name="big",
        created_at=utc_now(),
    )
    session.add(configuration)
    session.commit()
    return configuration


def spent(session: Session, feature: str, model: str, prompt: int, completion: int) -> None:
    session.add(
        AiCall(
            user_id=OWNER,
            occurred_at=utc_now(),
            run_id=f"run-{next(_counter)}",
            feature=feature,
            connection="Infomaniak",
            model=model,
            prompt_tokens=prompt,
            completion_tokens=completion,
        )
    )
    session.commit()


class TestBulk:
    def test_analyse_queues_open_documents_only(self, session: Session) -> None:
        configuration = add_configuration(session)
        ready = document(session, "ready.pdf")
        filed = document(session, "filed.pdf", status=DocumentStatus.moved)
        theirs = document(session, "theirs.pdf", user_id="someone-else")

        queued = inbox.analyse(
            session,
            OWNER,
            [ready.id, filed.id, theirs.id, "missing"],
            actor=TEST_ACTOR,
            configuration_id=configuration.id,
        )

        assert queued == 1
        session.refresh(ready)
        session.refresh(filed)
        assert ready.proposal_status is ProposalStatus.pending
        assert filed.proposal_status is ProposalStatus.ready

    def test_analyse_with_someone_elses_configuration_is_not_found(self, session: Session) -> None:
        theirs = add_configuration(session)
        theirs.user_id = "someone-else"
        session.commit()
        ready = document(session, "ready.pdf")

        with pytest.raises(NotFoundError):
            inbox.analyse(session, OWNER, [ready.id], actor=TEST_ACTOR, configuration_id=theirs.id)

    def test_one_failed_trash_does_not_stop_the_rest(
        self, session: Session, app_settings: AppSettings
    ) -> None:
        first = document(session, "first.pdf")
        stuck = document(session, "stuck.pdf")
        last = document(session, "last.pdf")
        webdav = FakeWebDav()
        webdav.fail_for = {"stuck.pdf"}

        trashed, failed = inbox.trash(
            session,
            webdav,  # type: ignore[arg-type]
            app_settings,
            [first.id, stuck.id, last.id],
            actor=TEST_ACTOR,
        )

        assert trashed == 2
        assert [failure.id for failure in failed] == [stuck.id]
        session.refresh(stuck)
        assert stuck.status is DocumentStatus.pending
        assert len(webdav.moves) == 2


class TestEstimate:
    def test_tokens_and_cost_from_past_calls_with_the_same_model(self, session: Session) -> None:
        configuration = add_configuration(session)
        spent(session, "filing", "big", 1000, 100)
        spent(session, "filing", "big", 3000, 300)
        spent(session, "filing", "other", 99_999, 9_999)
        ids = [document(session, f"{n}.pdf").id for n in range(3)]

        result = inbox.estimate(session, OWNER, ids, configuration.id)

        assert result.documents == 3
        assert result.readings_needed == 0
        assert (result.prompt_tokens, result.completion_tokens) == (6000, 600)
        # 6000 in at 1.00 and 600 out at 4.00 per million.
        assert result.cost == pytest.approx(0.0084)
        assert result.connections == ["Infomaniak"]

    def test_vision_readings_already_stored_are_not_counted(self, session: Session) -> None:
        configuration = add_configuration(session, vision=True)
        spent(session, "filing", "big", 1000, 100)
        spent(session, "extraction", "eyes", 2000, 500)
        read = document(session, "read.pdf")
        unread = document(session, "unread.pdf")
        plan = configurations.plan(session, configuration)
        session.add(
            Reading(
                user_id=OWNER,
                document_id=read.id,
                setup_key=plan.reading_setup(searchable=False).key,
                method=ReadingMethod.vision,
                label="x",
                text="x",
                created_at=utc_now(),
            )
        )
        session.commit()

        result = inbox.estimate(session, OWNER, [read.id, unread.id], configuration.id)

        assert result.readings_needed == 1
        assert result.prompt_tokens == 2 * 1000 + 2000

    def test_unknown_without_history_and_no_cost_without_a_price(self, session: Session) -> None:
        configuration = add_configuration(session)
        ids = [document(session, "a.pdf").id]
        result = inbox.estimate(session, OWNER, ids, configuration.id)
        assert (result.prompt_tokens, result.cost) == (None, None)

        spent(session, "filing", "other", 1000, 100)
        endpoint = session.get(AiEndpoint, configuration.filing_endpoint_id)
        assert endpoint is not None
        endpoint.model_prices = []
        session.commit()
        result = inbox.estimate(session, OWNER, ids, configuration.id)
        # Falls back to the feature's calls with any model, but there is no price to apply.
        assert (result.prompt_tokens, result.cost) == (1000, None)


class TestConnectionStatus:
    def test_probes_each_connection_once_a_minute(
        self, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in ("Tower", "Infomaniak"):
            session.add(
                AiEndpoint(
                    user_id=OWNER,
                    name=name,
                    base_url=f"https://{name}.test/v1",
                    created_at=utc_now(),
                )
            )
        session.commit()
        probed: list[str] = []

        def probe(*, endpoint_url: str, api_key: str | None) -> bool:
            probed.append(endpoint_url)
            return "Infomaniak" in endpoint_url

        monkeypatch.setattr(ai, "probe", probe)

        first = connections.statuses(session, OWNER)
        connections.statuses(session, OWNER)

        assert [(status.name, status.reachable) for status in first] == [
            ("Infomaniak", True),
            ("Tower", False),
        ]
        assert len(probed) == 2


class TestApi:
    def test_inbox_page_counts_only_the_callers_documents(self, client: TestClient) -> None:
        with Session(db.engine) as session:
            other = User(email="o@example.com", password_hash="x", created_at=datetime(2026, 9, 1))
            session.add(other)
            session.commit()
            document(session, "mine.pdf", user_id=signed_in_user_id())
            document(session, "theirs.pdf", user_id=other.id)

        body = client.get("/api/v1/inbox?group=ready").json()

        assert body["counts"] == {"analyse": 0, "ready": 1, "done": 0, "trashed": 0}
        (card,) = body["items"]
        assert card["original_filename"] == "mine.pdf"
        assert card["scanned_at"].endswith("+00:00")
        assert card["flags"] == []

    def test_unknown_group_is_refused(self, client: TestClient) -> None:
        assert client.get("/api/v1/inbox?group=everything").status_code == 422

    def test_thumbnail_is_private_jpeg(self, client: TestClient) -> None:
        with Session(db.engine) as session:
            target = document(session, "scan.pdf", user_id=signed_in_user_id())
            target_id = target.id
        fake = FakeWebDav()
        app.dependency_overrides[get_optional_webdav] = lambda: fake
        try:
            response = client.get(f"/api/v1/documents/{target_id}/thumbnail")
        finally:
            app.dependency_overrides.pop(get_optional_webdav, None)

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert response.headers["cache-control"] == "private, max-age=86400"

    def test_bulk_routes(self, client: TestClient) -> None:
        with Session(db.engine) as session:
            target = document(session, "scan.pdf", user_id=signed_in_user_id())
            target_id = target.id

        analysed = client.post("/api/v1/documents/analyse", json={"document_ids": [target_id]})
        assert analysed.json() == {"queued": 1}
        assert (
            client.post("/api/v1/documents/analyse", json={"document_ids": []}).status_code == 422
        )

        fake = FakeWebDav()
        app.dependency_overrides[get_webdav] = lambda: fake
        try:
            trashed = client.post("/api/v1/documents/trash", json={"document_ids": [target_id]})
        finally:
            app.dependency_overrides.pop(get_webdav, None)
        assert trashed.status_code == 200
        assert set(trashed.json()) == {"trashed", "failed"}

    def test_endpoint_status_route(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ai, "probe", lambda **_: True)
        client.post("/api/v1/ai/endpoints", json={"name": "Tower", "base_url": "https://t.test/v1"})

        (status,) = client.get("/api/v1/ai/endpoints/status").json()
        assert (status["name"], status["reachable"]) == ("Tower", True)


def test_folder_pattern_generalises_year_segments_only() -> None:
    assert inbox.folder_pattern("/Finance/WIR Bank/2026/") == "/finance/wir bank/{year}"
    assert inbox.folder_pattern("/Steuern/2026-Q1") == "/steuern/2026-q1"


def test_edit_distance_caps() -> None:
    assert inbox.edit_distance("7389534858", "7399534858") == 1
    assert inbox.edit_distance("12", "123456") == 3


def test_config_has_a_thumbnail_dir() -> None:
    assert config.thumbnail_cache_dir
