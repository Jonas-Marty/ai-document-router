"""Classical OCR, for comparison against what a vision model reads off the same page.

Tesseract is invoked as a subprocess against the PNGs `extraction.render_pages` already
produced, rather than through ocrmypdf. ocrmypdf's job is rewriting a PDF to carry a text
layer, which this app has no use for -- it wants the characters, not a new file -- and
buying that would pull ghostscript, qpdf, unpaper and pngquant into the image for nothing.
`tesseract-ocr` plus its language data is a fraction of the size.
"""

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

TESSERACT_BINARY = "tesseract"
# German first: the documents this was built for are Swiss business post. Tesseract takes
# several languages at once and picks per-block, so naming both costs only a little speed.
DEFAULT_LANGUAGES = "deu+eng"
# Per page. Tesseract on a 150 DPI A4 scan is a second or two; anything near this means
# something is wrong, and the poller must not be held up by it.
TIMEOUT_SECONDS = 60.0


class OcrUnavailable(Exception):
    """Tesseract is not installed, or could not read the page."""


def is_available() -> bool:
    """Whether the binary exists at all, so a caller can offer OCR only when it would work."""
    return shutil.which(TESSERACT_BINARY) is not None


def read_pages(pages: list[bytes], languages: str = DEFAULT_LANGUAGES) -> str:
    """Run Tesseract over rendered pages and return their concatenated text."""
    if not pages:
        return ""
    if not is_available():
        raise OcrUnavailable("Tesseract isn't installed in this image, so classical OCR can't run.")

    parts = [_read_one(page, index, languages) for index, page in enumerate(pages)]
    return "\n".join(part for part in parts if part).strip()


def _read_one(page: bytes, index: int, languages: str) -> str:
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "page.png"
        source.write_bytes(page)
        try:
            completed = subprocess.run(  # noqa: S603 - fixed binary, no shell, path we wrote
                [TESSERACT_BINARY, str(source), "stdout", "-l", languages],
                capture_output=True,
                timeout=TIMEOUT_SECONDS,
                check=False,
            )
        except FileNotFoundError as exc:
            raise OcrUnavailable("Tesseract isn't installed in this image.") from exc
        except subprocess.TimeoutExpired as exc:
            raise OcrUnavailable(f"Tesseract took longer than {TIMEOUT_SECONDS:.0f}s.") from exc

    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip().splitlines()
        raise OcrUnavailable(f"Tesseract failed: {detail[-1] if detail else 'no reason given'}")

    # A page that reads as nothing is not an error -- a blank second page is ordinary, and
    # the other pages still carry the document.
    text = completed.stdout.decode("utf-8", "replace").strip()
    if not text:
        logger.debug("Tesseract found no text on page %d", index)
    return text
