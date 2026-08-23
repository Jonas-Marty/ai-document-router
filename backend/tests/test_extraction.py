"""Extraction tests. PDFs are generated with pypdf so there are no binary fixtures."""

import io

import pytest
from pypdf import PdfWriter

from app.services import extraction
from app.services.extraction import NO_TEXT_LAYER_MESSAGE, extension_of, extract


def blank_pdf(pages: int = 1) -> bytes:
    """A structurally valid PDF with no text layer -- what an image-only scan looks like."""
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class TestExtension:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("scan.pdf", ".pdf"),
            ("scan.PDF", ".pdf"),
            ("a.b.tiff", ".tiff"),
            ("noextension", ""),
            ("trailingdot.", ""),
            ("2026.05.13 WIR Bank Bestätigung.pdf", ".pdf"),
        ],
    )
    def test_extension_of(self, filename: str, expected: str) -> None:
        assert extension_of(filename) == expected


class TestHashAndSize:
    def test_hash_is_stable_and_size_is_the_byte_count(self) -> None:
        data = b"hello world"

        first = extract(data, "x.bin")
        second = extract(data, "x.bin")

        assert first.content_hash == second.content_hash
        assert len(first.content_hash) == 64
        assert first.file_size_bytes == 11

    def test_different_bytes_hash_differently(self) -> None:
        assert extract(b"a", "x.bin").content_hash != extract(b"b", "x.bin").content_hash


class TestMime:
    def test_prefers_the_servers_content_type(self) -> None:
        assert extract(b"x", "scan.bin", "application/pdf").mime_type == "application/pdf"

    def test_falls_back_to_the_extension(self) -> None:
        assert extract(b"x", "scan.png").mime_type == "image/png"

    def test_unknown_extension_is_octet_stream(self) -> None:
        assert extract(b"x", "scan.weird").mime_type == "application/octet-stream"


class TestPdfs:
    def test_counts_pages(self) -> None:
        assert extract(blank_pdf(pages=3), "scan.pdf").page_count == 3

    def test_a_pdf_without_a_text_layer_fails_readably(self) -> None:
        """SPEC 6.3: this must not raise -- the document stays approvable by hand."""
        result = extract(blank_pdf(), "scan.pdf")

        assert result.text_error == NO_TEXT_LAYER_MESSAGE
        assert result.has_usable_text is False
        assert result.page_count == 1

    def test_corrupt_pdf_bytes_fail_readably(self) -> None:
        result = extract(b"%PDF-1.7 this is not really a pdf", "scan.pdf", "application/pdf")

        assert result.text_error is not None
        assert result.has_usable_text is False
        assert result.page_count is None

    def test_scanner_noise_below_the_threshold_counts_as_no_text_layer(self) -> None:
        """One real sample extracted 26 characters of nothing useful. Sending that to the
        model as if it were content produces a confident, wrong answer."""
        assert extraction.MIN_MEANINGFUL_TEXT_CHARS > 26


class TestImages:
    def test_images_have_no_page_count_and_no_text(self) -> None:
        result = extract(b"\x89PNG\r\n\x1a\n" + b"0" * 100, "scan.png")

        assert result.page_count is None
        assert result.text_error == NO_TEXT_LAYER_MESSAGE

    def test_pypdf_is_never_handed_a_non_pdf(self) -> None:
        # A 6 MB PNG reaching PdfReader would be a slow, noisy failure.
        result = extract(b"not a pdf at all", "photo.png")

        assert result.mime_type == "image/png"
        assert result.text_error == NO_TEXT_LAYER_MESSAGE
