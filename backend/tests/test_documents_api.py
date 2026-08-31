"""Tests for /queue, /documents/{id}, /content and /regenerate."""

from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import db
from app.deps import get_webdav
from app.main import app
from app.models import Document, DocumentStatus, Proposal, ProposalStatus
from app.services.errors import NotFoundError


def make_document(
    name: str,
    *,
    status: DocumentStatus = DocumentStatus.pending,
    skip_count: int = 0,
    scanned_at: datetime | None = None,
    proposal_status: ProposalStatus = ProposalStatus.ready,
) -> Document:
    return Document(
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


class TestQueue:
    def test_empty_queue(self, client: TestClient) -> None:
        response = client.get("/api/v1/queue")

        assert response.status_code == 200
        assert response.json() == {"items": [], "total_pending": 0}

    def test_returns_documents_with_their_proposal(self, client: TestClient) -> None:
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

        body = client.get("/api/v1/queue").json()

        assert body["total_pending"] == 1
        item = body["items"][0]
        assert item["original_filename"] == "scan.pdf"
        assert item["extension"] == ".pdf"
        assert item["proposal"]["suggested_name"] == "2026-08-21_Swisscom"
        assert item["proposal"]["confidence_score"] == 0.91

    def test_pending_always_precedes_skipped(self, client: TestClient) -> None:
        """SPEC 5: a skipped document never reappears before an unskipped one -- even
        when the skipped one was scanned much earlier."""
        with Session(db.engine) as session:
            session.add(
                make_document(
                    "old-skipped.pdf",
                    status=DocumentStatus.skipped,
                    skip_count=1,
                    scanned_at=datetime(2020, 1, 1),
                )
            )
            session.add(make_document("new-pending.pdf", scanned_at=datetime(2026, 8, 21, 12, 0)))
            session.commit()

        names = [item["original_filename"] for item in client.get("/api/v1/queue").json()["items"]]

        assert names == ["new-pending.pdf", "old-skipped.pdf"]

    def test_pending_ordered_by_scanned_at_ascending(self, client: TestClient) -> None:
        with Session(db.engine) as session:
            session.add(make_document("b.pdf", scanned_at=datetime(2026, 8, 21, 12, 0)))
            session.add(make_document("a.pdf", scanned_at=datetime(2026, 8, 20, 12, 0)))
            session.commit()

        names = [item["original_filename"] for item in client.get("/api/v1/queue").json()["items"]]

        assert names == ["a.pdf", "b.pdf"]

    def test_skipped_ordered_by_skip_count(self, client: TestClient) -> None:
        with Session(db.engine) as session:
            for count in (3, 1, 2):
                session.add(
                    make_document(f"s{count}.pdf", status=DocumentStatus.skipped, skip_count=count)
                )
            session.commit()

        names = [item["original_filename"] for item in client.get("/api/v1/queue").json()["items"]]

        assert names == ["s1.pdf", "s2.pdf", "s3.pdf"]

    def test_excludes_filed_documents(self, client: TestClient) -> None:
        with Session(db.engine) as session:
            session.add(make_document("moved.pdf", status=DocumentStatus.moved))
            session.add(make_document("trashed.pdf", status=DocumentStatus.trashed))
            session.add(make_document("pending.pdf"))
            session.commit()

        body = client.get("/api/v1/queue").json()

        assert body["total_pending"] == 1
        assert body["items"][0]["original_filename"] == "pending.pdf"

    def test_total_pending_counts_beyond_the_page(self, client: TestClient) -> None:
        with Session(db.engine) as session:
            for i in range(5):
                session.add(make_document(f"s{i}.pdf"))
            session.commit()

        body = client.get("/api/v1/queue?limit=2").json()

        assert len(body["items"]) == 2
        assert body["total_pending"] == 5

    def test_queue_depth_matches_total_pending(self, client: TestClient) -> None:
        """The outage banner and the queue screen must agree about how much work exists."""
        with Session(db.engine) as session:
            session.add(make_document("a.pdf"))
            session.add(make_document("b.pdf", status=DocumentStatus.skipped, skip_count=1))
            session.add(make_document("c.pdf", status=DocumentStatus.moved))
            session.commit()

        assert (
            client.get("/api/v1/health").json()["queue_depth"]
            == client.get("/api/v1/queue").json()["total_pending"]
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
