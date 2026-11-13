"""Tests for /documents/{id}, /content and /regenerate."""

from collections.abc import Iterator
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import db
from app.deps import get_webdav
from app.jobs import poller
from app.main import app
from app.models import Document, DocumentStatus, Proposal, ProposalStatus, User
from app.services import progress
from app.services.errors import NotFoundError
from tests.conftest import signed_in_user_id


def make_document(
    name: str,
    *,
    status: DocumentStatus = DocumentStatus.pending,
    skip_count: int = 0,
    scanned_at: datetime | None = None,
    proposal_status: ProposalStatus = ProposalStatus.ready,
    user_id: str | None = None,
) -> Document:
    return Document(
        user_id=user_id or signed_in_user_id(),
        webdav_path=f"/Test-Inbox/{name}",
        original_filename=name,
        mime_type="application/pdf",
        file_size_bytes=2048,
        page_count=1,
        content_hash=f"hash-{name}",
        scanned_at=scanned_at or datetime(2026, 8, 21, 9, 0),
        discovered_at=datetime(2026, 8, 21, 9, 5),
        status=status,
        skip_count=skip_count,
        proposal_status=proposal_status,
    )


class FakeWebDav:
    def __init__(self, body: bytes = b"%PDF-1.7 bytes", error: Exception | None = None):
        self.body = body
        self.error = error

    def read_stream(self, path: str, chunk_size: int = 65536) -> Iterator[bytes]:
        if self.error is not None:
            raise self.error
        yield self.body


@pytest.fixture
def webdav_override() -> Iterator[FakeWebDav]:
    fake = FakeWebDav()
    app.dependency_overrides[get_webdav] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_webdav, None)


class TestOwnership:
    def test_another_accounts_document_is_not_found(
        self, client: TestClient, webdav_override: FakeWebDav
    ) -> None:
        # 404 rather than 403: whether the id exists is itself none of this account's business.
        with Session(db.engine) as session:
            other = User(
                email="other@example.com", password_hash="x", created_at=datetime(2026, 9, 1)
            )
            session.add(other)
            session.commit()
            document = make_document("theirs.pdf", user_id=other.id)
            session.add(document)
            session.commit()
            document_id = document.id

        assert client.get(f"/api/v1/documents/{document_id}").status_code == 404
        assert client.get(f"/api/v1/documents/{document_id}/content").status_code == 404
        assert client.get("/api/v1/inbox").json()["items"] == []


class TestProposalShape:
    def test_returns_the_document_with_its_proposal(self, client: TestClient) -> None:
        with Session(db.engine) as session:
            document = make_document("scan.pdf")
            session.add(document)
            session.commit()
            session.add(
                Proposal(
                    document_id=document.id,
                    suggested_name="2026-08-21_Swisscom",
                    target_folder_path="/Documents/Finance",
                    document_date=None,
                    confidence_score=0.91,
                    reasoning_text="Invoice header.",
                    model_name="gpt-4o",
                    created_at=datetime(2026, 8, 21, 9, 6),
                )
            )
            session.commit()
            document_id = document.id

        item = client.get(f"/api/v1/documents/{document_id}").json()

        assert item["original_filename"] == "scan.pdf"
        assert item["extension"] == ".pdf"
        assert item["proposal"]["suggested_name"] == "2026-08-21_Swisscom"
        assert item["proposal"]["confidence_score"] == 0.91
        # Stored before name fields existed: a whole, free name (SPEC 7.1a).
        assert item["proposal"]["fields"] is None

    def test_a_field_proposal_returns_its_fields(self, client: TestClient) -> None:
        with Session(db.engine) as session:
            document = make_document("scan.pdf")
            session.add(document)
            session.commit()
            session.add(
                Proposal(
                    document_id=document.id,
                    suggested_name="2026.05.13 WIR Bank Konto",
                    target_folder_path="/Documents/Bank",
                    document_date=date(2026, 5, 13),
                    sender="WIR Bank",
                    title="Konto",
                    confidence_score=0.9,
                    reasoning_text="Letterhead.",
                    model_name="m",
                    created_at=datetime(2026, 5, 13, 9, 6),
                )
            )
            session.commit()
            document_id = document.id

        item = client.get(f"/api/v1/documents/{document_id}").json()

        assert item["proposal"]["fields"] == {
            "sender": "WIR Bank",
            "account": None,
            "title": "Konto",
            "period": None,
            "number": None,
        }

    def test_queue_depth_counts_the_open_groups(self, client: TestClient) -> None:
        """The outage banner and the Inbox must agree about how much work exists."""
        with Session(db.engine) as session:
            session.add(make_document("a.pdf"))
            session.add(make_document("b.pdf", status=DocumentStatus.skipped, skip_count=1))
            session.add(make_document("p.pdf", proposal_status=ProposalStatus.pending))
            session.add(make_document("c.pdf", status=DocumentStatus.moved))
            session.commit()

        counts = client.get("/api/v1/inbox").json()["counts"]
        assert client.get("/api/v1/health").json()["queue_depth"] == (
            counts["analyse"] + counts["ready"]
        )


class TestGetDocument:
    def test_returns_a_document(self, client: TestClient) -> None:
        with Session(db.engine) as session:
            document = make_document("scan.pdf")
            session.add(document)
            session.commit()
            document_id = document.id

        body = client.get(f"/api/v1/documents/{document_id}").json()

        assert body["id"] == document_id
        assert body["proposal"] is None

    def test_unknown_id_is_a_404_envelope(self, client: TestClient) -> None:
        response = client.get("/api/v1/documents/does-not-exist")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_scanned_at_serialises_with_a_timezone(self, client: TestClient) -> None:
        """SQLite drops tzinfo. Without re-attaching UTC the frontend would read the
        instant as local time and show the wrong relative age."""
        with Session(db.engine) as session:
            document = make_document("scan.pdf", scanned_at=datetime(2026, 8, 21, 9, 0))
            session.add(document)
            session.commit()
            document_id = document.id

        scanned_at = client.get(f"/api/v1/documents/{document_id}").json()["scanned_at"]

        assert scanned_at.endswith("+00:00") or scanned_at.endswith("Z")


class TestContent:
    def test_streams_the_file(self, client: TestClient, webdav_override: FakeWebDav) -> None:
        with Session(db.engine) as session:
            document = make_document("scan.pdf")
            session.add(document)
            session.commit()
            document_id = document.id

        response = client.get(f"/api/v1/documents/{document_id}/content")

        assert response.status_code == 200
        assert response.content == b"%PDF-1.7 bytes"
        assert response.headers["content-type"].startswith("application/pdf")

    def test_a_missing_file_becomes_a_404_not_a_truncated_200(
        self, client: TestClient, webdav_override: FakeWebDav
    ) -> None:
        """read_stream is lazy, so without resolving the first chunk eagerly the 200
        headers would already be sent when the error surfaced."""
        webdav_override.error = NotFoundError("That file is no longer on the server.")
        with Session(db.engine) as session:
            document = make_document("gone.pdf")
            session.add(document)
            session.commit()
            document_id = document.id

        response = client.get(f"/api/v1/documents/{document_id}/content")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


class TestRegenerate:
    def test_resets_the_document_to_pending(self, client: TestClient) -> None:
        with Session(db.engine) as session:
            document = make_document("scan.pdf", proposal_status=ProposalStatus.failed)
            document.proposal_error = "No text layer found — OCR isn't set up yet."
            session.add(document)
            session.commit()
            document_id = document.id

        body = client.post(f"/api/v1/documents/{document_id}/regenerate").json()

        assert body["document"]["proposal_status"] == "pending"
        assert body["document"]["proposal_error"] is None

    def test_unknown_id_is_a_404(self, client: TestClient) -> None:
        assert client.post("/api/v1/documents/nope/regenerate").status_code == 404

    def configuration(self, client: TestClient, name: str) -> str:
        endpoint = client.get("/api/v1/ai/endpoints").json()
        endpoint_id = (
            endpoint[0]["id"]
            if endpoint
            else client.post(
                "/api/v1/ai/endpoints", json={"name": "Hosted", "base_url": "https://h.test/v1"}
            ).json()["id"]
        )
        response = client.post(
            "/api/v1/configurations",
            json={
                "name": name,
                "reading_method": "text_layer",
                "reading": {"temperature": 0.1, "max_tokens": 4096},
                "filing_strategy": "one_call",
                "filing": {
                    "endpoint_id": endpoint_id,
                    "model_name": "big",
                    "temperature": 0.1,
                    "max_tokens": 2048,
                },
                "max_examples": 8,
                "max_folders": 250,
                "max_text_chars": 6000,
            },
        )
        assert response.status_code == 201, response.text
        return str(response.json()["id"])

    def queued(self) -> str:
        with Session(db.engine) as session:
            document = make_document("scan.pdf")
            session.add(document)
            session.commit()
            return document.id

    def test_a_picked_configuration_is_kept_for_the_document(self, client: TestClient) -> None:
        self.configuration(client, "Daily")
        careful = self.configuration(client, "Careful")
        document_id = self.queued()

        response = client.post(
            f"/api/v1/documents/{document_id}/regenerate", json={"configuration_id": careful}
        )

        assert response.status_code == 200
        with Session(db.engine) as session:
            stored = session.get(Document, document_id)
            assert stored is not None
            assert stored.configuration_id == careful

    def test_picking_the_default_clears_an_earlier_pick(self, client: TestClient) -> None:
        """So the document follows the default when someone later changes which one it is."""
        daily = self.configuration(client, "Daily")
        careful = self.configuration(client, "Careful")
        document_id = self.queued()
        client.post(
            f"/api/v1/documents/{document_id}/regenerate", json={"configuration_id": careful}
        )

        client.post(f"/api/v1/documents/{document_id}/regenerate", json={"configuration_id": daily})

        with Session(db.engine) as session:
            stored = session.get(Document, document_id)
            assert stored is not None
            assert stored.configuration_id is None

    def test_a_plain_rerun_keeps_the_pick(self, client: TestClient) -> None:
        self.configuration(client, "Daily")
        careful = self.configuration(client, "Careful")
        document_id = self.queued()
        client.post(
            f"/api/v1/documents/{document_id}/regenerate", json={"configuration_id": careful}
        )

        client.post(f"/api/v1/documents/{document_id}/regenerate")

        with Session(db.engine) as session:
            stored = session.get(Document, document_id)
            assert stored is not None
            assert stored.configuration_id == careful

    def test_an_unknown_configuration_is_a_404(self, client: TestClient) -> None:
        document_id = self.queued()

        response = client.post(
            f"/api/v1/documents/{document_id}/regenerate", json={"configuration_id": "nope"}
        )

        assert response.status_code == 404

    def test_starts_the_poller_now_rather_than_at_the_next_interval(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        kicked: list[bool] = []
        monkeypatch.setattr(poller, "kick", lambda: kicked.append(True))
        with Session(db.engine) as session:
            document = make_document("scan.pdf")
            session.add(document)
            session.commit()
            document_id = document.id

        client.post(f"/api/v1/documents/{document_id}/regenerate")

        assert kicked == [True]

    def test_reports_what_the_analysis_is_doing(self, client: TestClient) -> None:
        with Session(db.engine) as session:
            document = make_document("scan.pdf", proposal_status=ProposalStatus.pending)
            session.add(document)
            session.commit()
            document_id = document.id

        with progress.tracking(document_id):
            progress.report("Reading the document with Jonas-PC · chandra-ocr-2…")
            during = client.get(f"/api/v1/documents/{document_id}").json()
        after = client.get(f"/api/v1/documents/{document_id}").json()

        assert during["analysis_stage"].startswith("Reading the document")
        assert after["analysis_stage"] is None


class TestRetryFailed:
    """The poller only looks at pending documents, so a failed proposal stays failed for
    good. When the *configuration* was the problem -- no allowed folders, a rejected AI
    request -- one fix in Settings has to be able to make all of them retryable at once."""

    def _add(self, name: str, **kwargs: object) -> str:
        with Session(db.engine) as session:
            document = make_document(name, **kwargs)  # type: ignore[arg-type]
            document.proposal_error = "No allowed folders are configured yet."
            session.add(document)
            session.commit()
            return document.id

    def test_resets_every_failed_proposal_in_the_queue(self, client: TestClient) -> None:
        first = self._add("a.pdf", proposal_status=ProposalStatus.failed)
        second = self._add("b.pdf", proposal_status=ProposalStatus.failed)

        body = client.post("/api/v1/documents/retry-failed").json()

        assert body["retried"] == 2
        for document_id in (first, second):
            document = client.get(f"/api/v1/documents/{document_id}").json()
            assert document["proposal_status"] == "pending"
            assert document["proposal_error"] is None

    def test_leaves_ready_proposals_alone(self, client: TestClient) -> None:
        ready = self._add("ready.pdf", proposal_status=ProposalStatus.ready)

        assert client.post("/api/v1/documents/retry-failed").json()["retried"] == 0
        assert client.get(f"/api/v1/documents/{ready}").json()["proposal_status"] == "ready"

    def test_leaves_already_filed_documents_alone(self, client: TestClient) -> None:
        """Their proposal_status survives filing, and re-proposing one would spend an LLM
        call on a document nobody is going to review again."""
        moved = self._add(
            "moved.pdf", status=DocumentStatus.moved, proposal_status=ProposalStatus.failed
        )

        assert client.post("/api/v1/documents/retry-failed").json()["retried"] == 0
        assert client.get(f"/api/v1/documents/{moved}").json()["proposal_status"] == "failed"

    def test_retries_a_skipped_document(self, client: TestClient) -> None:
        """Skipped is still in the queue -- it is at the back of it, not out of it."""
        self._add(
            "skipped.pdf",
            status=DocumentStatus.skipped,
            proposal_status=ProposalStatus.failed,
        )

        assert client.post("/api/v1/documents/retry-failed").json()["retried"] == 1

    def test_is_a_no_op_when_nothing_failed(self, client: TestClient) -> None:
        assert client.post("/api/v1/documents/retry-failed").json()["retried"] == 0

    def test_needs_a_session(self, anonymous_client: TestClient) -> None:
        assert anonymous_client.post("/api/v1/documents/retry-failed").status_code == 401
