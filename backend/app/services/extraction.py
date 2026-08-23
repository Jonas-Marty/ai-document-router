"""Pull text and metadata out of a downloaded document.

Measured against 52 real scanner outputs from the target Nextcloud: 49 carry a usable text
layer, 3 do not. So the no-text-layer path is a genuine minority case rather than the norm,
but it is common enough that it must fail readably instead of raising.
"""

import hashlib
import io
import logging
import mimetypes
from dataclasses import dataclass

from pypdf import PdfReader
from pypdf.errors import PdfReadError

logger = logging.getLogger(__name__)

# SPEC 6.3: the model sees the first 6000 characters.
MAX_TEXT_CHARS = 6000

# Below this, a "text layer" is scanner noise -- a stray glyph or a page number -- and not
# enough to identify a document. One real sample in the survey extracted 26 characters of
# nothing useful, which would otherwise have been sent to the model as if it were content.
MIN_MEANINGFUL_TEXT_CHARS = 50

PDF_MIME = "application/pdf"
NO_TEXT_LAYER_MESSAGE = "No text layer found — OCR isn't set up yet."


@dataclass(frozen=True)
class ExtractedDocument:
    """What we learned from a document's bytes."""

    content_hash: str
    file_size_bytes: int
    mime_type: str
    page_count: int | None
    text: str
    text_error: str | None

    @property
    def has_usable_text(self) -> bool:
        return self.text_error is None and len(self.text) >= MIN_MEANINGFUL_TEXT_CHARS


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def guess_mime_type(filename: str, server_content_type: str | None = None) -> str:
    """Prefer what the server told us; fall back to the extension."""
    if server_content_type:
        return server_content_type
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def extension_of(filename: str) -> str:
    """The extension including the dot, lowercased. '' when there isn't one.

    SPEC 4.2 carries this on every Document; it is never editable, so it is derived from the
    original filename rather than stored.
    """
    _, dot, suffix = filename.rpartition(".")
    if not dot or not suffix:
        return ""
    return f".{suffix.lower()}"


def extract(
    data: bytes, filename: str, server_content_type: str | None = None
) -> ExtractedDocument:
    """Extract text and metadata. Never raises for a malformed or text-free document."""
    mime_type = guess_mime_type(filename, server_content_type)
    content_hash = sha256(data)
    size = len(data)

    def build(page_count: int | None, text: str, text_error: str | None) -> ExtractedDocument:
        return ExtractedDocument(
            content_hash=content_hash,
            file_size_bytes=size,
            mime_type=mime_type,
            page_count=page_count,
            text=text,
            text_error=text_error,
        )

    if mime_type != PDF_MIME:
        # Images have no pages and no text layer. SPEC 4.1: page_count is null for images.
        return build(page_count=None, text="", text_error=NO_TEXT_LAYER_MESSAGE)

    try:
        reader = PdfReader(io.BytesIO(data))
        page_count = len(reader.pages)
    except (PdfReadError, ValueError, OSError) as exc:
        logger.warning("Could not read PDF %r: %s", filename, exc)
        return build(page_count=None, text="", text_error=f"This PDF couldn't be read: {exc}")

    text = _extract_text(reader, filename)
    if len(text) < MIN_MEANINGFUL_TEXT_CHARS:
        return build(page_count=page_count, text=text, text_error=NO_TEXT_LAYER_MESSAGE)

    return build(page_count=page_count, text=text[:MAX_TEXT_CHARS], text_error=None)


def _extract_text(reader: PdfReader, filename: str) -> str:
    """Concatenate page text, stopping once we have enough for the prompt.

    A single unreadable page does not sink the document: scanners produce mixed files, and
    partial text is still enough for the model to name the thing.
    """
    parts: list[str] = []
    total = 0
    for index, page in enumerate(reader.pages):
        try:
            page_text = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001 - pypdf raises broadly on damaged pages
            logger.debug("Page %d of %r failed to extract: %s", index, filename, exc)
            continue
        parts.append(page_text)
        total += len(page_text)
        if total >= MAX_TEXT_CHARS:
            break
    return "\n".join(parts).strip()
