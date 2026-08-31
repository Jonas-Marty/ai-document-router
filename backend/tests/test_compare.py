"""Tests for reading one document several ways and reporting what each proposed.

The point of the feature is judgement: someone is deciding whether a vision model earns its
keep over the text layer, so a method that fails has to come back as a *result* with a
reason, never be quietly dropped from the list.
"""

import io
from typing import Any

import pytest
from pypdf import PdfWriter

from app.models import AppSettings
from app.services import compare, extraction, ocr

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
        "ai_endpoint_url": "https://ai.example.com/v1",
        "ai_model_name": "text-model",
        "vision_model_names": [],
    }
    return AppSettings(**{**base, **overrides})


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


def run(monkeypatch: pytest.MonkeyPatch, *, data: bytes, app_settings: AppSettings, replies=None):
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
    results = compare.compare(FakeWebDav(data), app_settings, FakeDocument())  # type: ignore[arg-type]
    return results, sent


def test_reports_a_result_for_every_configured_method(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocr, "is_available", lambda: False)
    results, _ = run(
        monkeypatch,
        data=blank_pdf(),
        app_settings=settings(vision_model_names=["qwen-vl", "llava"]),
    )

    assert [result.method for result in results] == [
        compare.TEXT_LAYER,
        compare.OCR,
        compare.VISION,
        compare.VISION,
    ]


def test_a_method_that_cannot_run_is_reported_not_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """ "Tesseract isn't installed" is the finding, not an absence to puzzle over."""
    monkeypatch.setattr(ocr, "is_available", lambda: False)
    results, _ = run(monkeypatch, data=blank_pdf(), app_settings=settings())

    tesseract = next(r for r in results if r.method == compare.OCR)
    assert tesseract.proposal is None
    assert tesseract.error is not None
    assert "Tesseract" in tesseract.error


def test_a_blank_scan_reports_the_text_layer_as_unusable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocr, "is_available", lambda: False)
    results, sent = run(monkeypatch, data=blank_pdf(), app_settings=settings())

    text_layer = next(r for r in results if r.method == compare.TEXT_LAYER)
    assert text_layer.proposal is None
    assert text_layer.error == extraction.NO_TEXT_LAYER_MESSAGE
    # And it did not spend an LLM call to find that out.
    assert sent == []


def test_the_vision_method_sends_pages_and_no_transcription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Handing the model text as well as the image would make it impossible to tell which
    of the two it actually used."""
    monkeypatch.setattr(ocr, "is_available", lambda: False)
    _, sent = run(
        monkeypatch, data=blank_pdf(2), app_settings=settings(vision_model_names=["qwen-vl"])
    )

    vision = next(call for call in sent if call["model_name"] == "qwen-vl")
    assert vision["images"] is not None
    assert len(vision["images"]) == 2
    assert all(image.startswith(b"\x89PNG") for image in vision["images"])
    assert "(no text could be extracted)" in vision["prompt"]


def test_ocr_text_is_proposed_with_the_configured_text_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ocr, "is_available", lambda: True)
    monkeypatch.setattr(ocr, "read_pages", lambda pages, **kw: "Helvetia Versicherungspolice")
    results, sent = run(monkeypatch, data=blank_pdf(), app_settings=settings())

    tesseract = next(r for r in results if r.method == compare.OCR)
    assert tesseract.proposal is not None
    assert tesseract.text_preview == "Helvetia Versicherungspolice"
    ocr_call = next(call for call in sent if "Helvetia Versicherungspolice" in call["prompt"])
    # Text, not pixels: this method's whole claim is that Tesseract did the reading.
    assert ocr_call["images"] is None


def test_a_rejected_proposal_becomes_that_method_s_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.ai import ProposalRejected

    monkeypatch.setattr(ocr, "is_available", lambda: True)
    monkeypatch.setattr(ocr, "read_pages", lambda pages, **kw: "some text")

    def replies(kwargs: dict[str, Any]):
        return ProposalRejected("The model chose '/Nope', which is outside your folders.")

    results, _ = run(monkeypatch, data=blank_pdf(), app_settings=settings(), replies=replies)

    tesseract = next(r for r in results if r.method == compare.OCR)
    assert tesseract.proposal is None
    assert "outside your folders" in (tesseract.error or "")


def test_a_non_pdf_cannot_be_rendered_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocr, "is_available", lambda: True)
    results, _ = run(
        monkeypatch,
        data=b"\xff\xd8\xff not a pdf",
        app_settings=settings(vision_model_names=["qwen-vl"]),
    )

    for result in results:
        if result.method in (compare.OCR, compare.VISION):
            assert result.proposal is None
            assert "rendered" in (result.error or "")


def test_nothing_is_written_back_to_the_document(monkeypatch: pytest.MonkeyPatch) -> None:
    """Comparing is a read. The document's own proposal is decided by the poller and the
    person, never by having looked at alternatives."""
    monkeypatch.setattr(ocr, "is_available", lambda: False)
    document = FakeDocument()
    before = vars(document).copy()

    monkeypatch.setattr(compare.ai, "request_proposal", lambda **kw: None)
    compare.compare(FakeWebDav(blank_pdf()), settings(), document)  # type: ignore[arg-type]

    assert vars(document) == before


def test_render_pages_produces_one_png_per_page() -> None:
    pages = extraction.render_pages(blank_pdf(3), max_pages=2)

    assert len(pages) == 2
    assert all(page.startswith(b"\x89PNG") for page in pages)


def test_render_pages_rejects_something_that_is_not_a_pdf() -> None:
    with pytest.raises(extraction.RenderUnavailable):
        extraction.render_pages(b"definitely not a pdf")
