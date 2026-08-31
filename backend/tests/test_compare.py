"""Tests for reading one document several ways and reporting what each proposed.

The point of the feature is judgement: someone is deciding whether a vision model earns its
keep over the text layer, so a method that fails has to come back as a *result* with a
reason, never be quietly dropped from the list.
"""

import io
from collections.abc import Iterator
from typing import Any

import pytest
from pypdf import PdfWriter
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import AiEndpoint, AiTask, AiTaskStep, AppSettings
from app.services import compare, extraction, ocr
from app.services.times import utc_now

VALID_REPLY = {
    "suggested_name": "2026.04.16 Helvetia Police",
    "target_folder_path": "/Documents/Insurance",
    "document_date": "2026-04-16",
    "confidence_score": 0.88,
    "reasoning_text": "Letterhead reads Helvetia.",
}


def blank_pdf(pages: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def settings(**overrides: Any) -> AppSettings:
    base = {
        "id": 1,
        "allowed_root_folders": ["/Documents"],
        "trash_folder_path": "/Trash",
    }
    return AppSettings(**{**base, **overrides})


@pytest.fixture
def session() -> Iterator[Session]:
    """A filing chain, and no extraction chain unless a test adds one."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(
            AiEndpoint(
                id="ep", name="Local", base_url="https://ai.example.com/v1", created_at=utc_now()
            )
        )
        s.add(AiTaskStep(task=AiTask.filing, position=0, endpoint_id="ep", model_name="text-model"))
        s.commit()
        yield s


def with_extraction(session: Session, model_name: str = "qwen-vl") -> None:
    session.add(
        AiTaskStep(task=AiTask.extraction, position=0, endpoint_id="ep", model_name=model_name)
    )
    session.commit()


class FakeWebDav:
    def __init__(self, body: bytes):
        self.body = body

    def read_stream(self, path: str, chunk_size: int = 65536):  # noqa: ANN201
        yield self.body

    def list_dir(self, path: str):  # noqa: ANN201
        return []


class FakeDocument:
    def __init__(self, filename: str = "scan.pdf", mime_type: str = "application/pdf"):
        self.webdav_path = f"/Test-Inbox/{filename}"
        self.original_filename = filename
        self.mime_type = mime_type


def run(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
    *,
    data: bytes,
    app_settings: AppSettings,
    replies=None,
):
    """Run compare with the LLM stubbed, capturing what each method actually sent."""
    sent: list[dict[str, Any]] = []

    def fake_request_proposal(**kwargs: Any):
        sent.append(kwargs)
        if replies is not None:
            reply = replies(kwargs)
            if isinstance(reply, Exception):
                raise reply
        from datetime import date

        from app.services.ai import Proposal

        return Proposal(
            suggested_name=VALID_REPLY["suggested_name"],
            target_folder_path=VALID_REPLY["target_folder_path"],
            document_date=date(2026, 4, 16),
            confidence_score=0.88,
            reasoning_text=VALID_REPLY["reasoning_text"],
            model_name=kwargs["model_name"],
        )

    monkeypatch.setattr(compare.ai, "request_proposal", fake_request_proposal)
    results = compare.compare(session, FakeWebDav(data), app_settings, FakeDocument())  # type: ignore[arg-type]
    return results, sent


def test_reports_a_result_for_every_method(
    monkeypatch: pytest.MonkeyPatch, session: Session
) -> None:
    monkeypatch.setattr(ocr, "is_available", lambda: False)
    with_extraction(session)
    monkeypatch.setattr(
        compare.transcribe,
        "transcribe",
        lambda data, steps: compare.transcribe.Transcription(
            markdown="# Helvetia", model_label="Local · qwen-vl"
        ),
    )
    results, _ = run(monkeypatch, session, data=blank_pdf(), app_settings=settings())

    assert [result.method for result in results] == [
        compare.TEXT_LAYER,
        compare.OCR,
        compare.MARKDOWN,
    ]


def test_a_method_that_cannot_run_is_reported_not_dropped(
    monkeypatch: pytest.MonkeyPatch, session: Session
) -> None:
    """ "Tesseract isn't installed" is the finding, not an absence to puzzle over."""
    monkeypatch.setattr(ocr, "is_available", lambda: False)
    results, _ = run(monkeypatch, session, data=blank_pdf(), app_settings=settings())

    tesseract = next(r for r in results if r.method == compare.OCR)
    assert tesseract.proposal is None
    assert tesseract.error is not None
    assert "Tesseract" in tesseract.error


def test_a_blank_scan_reports_the_text_layer_as_unusable(
    monkeypatch: pytest.MonkeyPatch, session: Session
) -> None:
    monkeypatch.setattr(ocr, "is_available", lambda: False)
    results, sent = run(monkeypatch, session, data=blank_pdf(), app_settings=settings())

    text_layer = next(r for r in results if r.method == compare.TEXT_LAYER)
    assert text_layer.proposal is None
    assert text_layer.error == extraction.NO_TEXT_LAYER_MESSAGE
    # And it did not spend an LLM call to find that out.
    assert sent == []


def test_the_markdown_method_files_from_the_transcription(
    monkeypatch: pytest.MonkeyPatch, session: Session
) -> None:
    """The comparison is of readings, not of models: the filing model is the same one the
    other methods used, so a difference in the result is a difference in what it was given."""
    monkeypatch.setattr(ocr, "is_available", lambda: False)
    with_extraction(session)
    monkeypatch.setattr(
        compare.transcribe,
        "transcribe",
        lambda data, steps: compare.transcribe.Transcription(
            markdown="# Helvetia\n\n| Premium | 480.00 |", model_label="Local · qwen-vl"
        ),
    )
    results, sent = run(monkeypatch, session, data=blank_pdf(), app_settings=settings())

    markdown = next(r for r in results if r.method == compare.MARKDOWN)
    assert markdown.proposal is not None
    assert "| Premium | 480.00 |" in markdown.text_preview
    assert "qwen-vl" in markdown.label
    call = next(c for c in sent if "| Premium | 480.00 |" in c["prompt"])
    assert call["model_name"] == "text-model"


def test_the_markdown_method_says_when_no_extraction_endpoint_is_assigned(
    monkeypatch: pytest.MonkeyPatch, session: Session
) -> None:
    monkeypatch.setattr(ocr, "is_available", lambda: False)
    results, _ = run(monkeypatch, session, data=blank_pdf(), app_settings=settings())

    markdown = next(r for r in results if r.method == compare.MARKDOWN)
    assert markdown.proposal is None
    assert "extraction task" in (markdown.error or "")


def test_ocr_text_is_proposed_with_the_configured_text_model(
    monkeypatch: pytest.MonkeyPatch, session: Session
) -> None:
    monkeypatch.setattr(ocr, "is_available", lambda: True)
    monkeypatch.setattr(ocr, "read_pages", lambda pages, **kw: "Helvetia Versicherungspolice")
    results, sent = run(monkeypatch, session, data=blank_pdf(), app_settings=settings())

    tesseract = next(r for r in results if r.method == compare.OCR)
    assert tesseract.proposal is not None
    assert tesseract.text_preview == "Helvetia Versicherungspolice"
    ocr_call = next(call for call in sent if "Helvetia Versicherungspolice" in call["prompt"])
    # Text, not pixels: this method's whole claim is that Tesseract did the reading.
    assert "images" not in ocr_call


def test_a_rejected_proposal_becomes_that_method_s_error(
    monkeypatch: pytest.MonkeyPatch, session: Session
) -> None:
    from app.services.ai import ProposalRejected

    monkeypatch.setattr(ocr, "is_available", lambda: True)
    monkeypatch.setattr(ocr, "read_pages", lambda pages, **kw: "some text")

    def replies(kwargs: dict[str, Any]):
        return ProposalRejected("The model chose '/Nope', which is outside your folders.")

    results, _ = run(
        monkeypatch, session, data=blank_pdf(), app_settings=settings(), replies=replies
    )

    tesseract = next(r for r in results if r.method == compare.OCR)
    assert tesseract.proposal is None
    assert "outside your folders" in (tesseract.error or "")


def test_a_non_pdf_cannot_be_rendered_and_says_so(
    monkeypatch: pytest.MonkeyPatch, session: Session
) -> None:
    monkeypatch.setattr(ocr, "is_available", lambda: True)
    with_extraction(session)
    results, _ = run(monkeypatch, session, data=b"\xff\xd8\xff not a pdf", app_settings=settings())

    for result in results:
        if result.method in (compare.OCR, compare.MARKDOWN):
            assert result.proposal is None
            assert "rendered" in (result.error or "")


def test_nothing_is_written_back_to_the_document(
    monkeypatch: pytest.MonkeyPatch, session: Session
) -> None:
    """Comparing is a read. The document's own proposal is decided by the poller and the
    person, never by having looked at alternatives."""
    monkeypatch.setattr(ocr, "is_available", lambda: False)
    document = FakeDocument()
    before = vars(document).copy()

    monkeypatch.setattr(compare.ai, "request_proposal", lambda **kw: None)
    compare.compare(session, FakeWebDav(blank_pdf()), settings(), document)  # type: ignore[arg-type]

    assert vars(document) == before


def test_render_pages_produces_one_png_per_page() -> None:
    pages = extraction.render_pages(blank_pdf(3), max_pages=2)

    assert len(pages) == 2
    assert all(page.startswith(b"\x89PNG") for page in pages)


def test_render_pages_rejects_something_that_is_not_a_pdf() -> None:
    with pytest.raises(extraction.RenderUnavailable):
        extraction.render_pages(b"definitely not a pdf")
