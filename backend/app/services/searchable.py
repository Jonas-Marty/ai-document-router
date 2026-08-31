"""Turn a scanned document into a searchable one, and keep it until it is filed.

`ocr.py` deliberately avoids ocrmypdf: it wants the characters off a page, and rendering
with pypdfium2 and piping PNGs through the tesseract CLI gets those for a fraction of the
image size. This module wants the opposite thing -- a *file* that carries a text layer, to
be stored in place of the original -- and that is precisely ocrmypdf's job. Doing it by hand
means composing a PDF around invisible positioned glyphs, which is not a thing to reinvent
for an archive somebody has to be able to read in ten years.

Two flags carry the whole safety argument:

  --output-type pdf   Graft a text layer onto the original page images with pikepdf, rather
                      than rewriting the file through ghostscript. The pixels that come out
                      are the pixels that went in; only invisible text is added.
  --skip-text         Leave pages that already have text alone. Belt and braces -- callers
                      already check for a text layer -- but it also covers the mixed file a
                      scanner produces when someone feeds it a printout and a photocopy.

The result is cached on the data volume rather than uploaded immediately, because producing
it takes tens of seconds to minutes and approve is synchronous with a person waiting on it
(SPEC 6.4). The poller does the work; approve only uploads what is already sitting there.
"""

import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from app.config import settings as config

logger = logging.getLogger(__name__)

BINARY = "ocrmypdf"
# The same pairing as ocr.py, for the same reason: Swiss business post is mostly German with
# enough English in it that naming only one loses documents.
DEFAULT_LANGUAGES = "deu+eng"

# ocrmypdf exit codes worth naming. Everything else is reported with whatever it said on
# stderr, which is generally readable enough to act on.
_EXIT_ALREADY_HAS_TEXT = 6
_EXIT_ENCRYPTED = 4


class SearchableUnavailable(Exception):
    """No searchable copy could be produced. The original is still perfectly fine."""


def is_available() -> bool:
    """Whether ocrmypdf is installed, so a caller can offer this only when it would work."""
    return shutil.which(BINARY) is not None


def build(data: bytes, languages: str = DEFAULT_LANGUAGES) -> bytes:
    """OCR a PDF and return the same document with a text layer added."""
    if not is_available():
        raise SearchableUnavailable("ocrmypdf isn't installed in this image.")

    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "in.pdf"
        target = Path(directory) / "out.pdf"
        source.write_bytes(data)

        try:
            completed = subprocess.run(  # noqa: S603 - fixed binary, no shell, paths we wrote
                [
                    BINARY,
                    "--output-type",
                    "pdf",
                    "--skip-text",
                    # No pngquant, no jbig2enc, no re-encoding of the scan. Optimisation is
                    # not worth the risk of changing what an archived document looks like.
                    "--optimize",
                    "0",
                    "--quiet",
                    "-l",
                    languages,
                    str(source),
                    str(target),
                ],
                capture_output=True,
                timeout=config.ocr_timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise SearchableUnavailable("ocrmypdf isn't installed in this image.") from exc
        except subprocess.TimeoutExpired as exc:
            raise SearchableUnavailable(
                f"OCR took longer than {config.ocr_timeout_seconds:.0f}s."
            ) from exc

        if completed.returncode == _EXIT_ALREADY_HAS_TEXT:
            raise SearchableUnavailable("This document already has a text layer.")
        if completed.returncode == _EXIT_ENCRYPTED:
            raise SearchableUnavailable("This PDF is encrypted, so it can't be OCR'd.")
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", "replace").strip().splitlines()
            raise SearchableUnavailable(
                f"OCR failed: {detail[-1] if detail else 'no reason given'}"
            )
        if not target.exists():
            raise SearchableUnavailable("OCR produced no output.")

        return target.read_bytes()


# -- cache ------------------------------------------------------------------------------
#
# Keyed by content hash, not document id: the same bytes only ever need OCRing once, and a
# hash that no longer matches the file is a cache entry that correctly stops being found.


def _directory() -> Path:
    path = Path(config.ocr_cache_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _path_for(content_hash: str) -> Path:
    # Hex only, so a hash read back out of the database can never walk out of the directory.
    if not content_hash or not all(character in "0123456789abcdef" for character in content_hash):
        raise ValueError(f"Not a content hash: {content_hash!r}")
    return _directory() / f"{content_hash}.pdf"


def store(content_hash: str, data: bytes) -> None:
    """Cache a searchable copy.

    Written to a temporary name in the same directory and renamed into place, so a crash
    mid-write leaves no half-file for approve to upload over a perfectly good original.
    """
    target = _path_for(content_hash)
    handle, temporary = tempfile.mkstemp(dir=target.parent, suffix=".part")
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(data)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def load(content_hash: str) -> bytes | None:
    """The cached copy, or None when there isn't one. Never raises for a missing file."""
    try:
        return _path_for(content_hash).read_bytes()
    except (OSError, ValueError) as exc:
        logger.debug("No cached searchable copy for %s: %s", content_hash, exc)
        return None


def discard(content_hash: str) -> None:
    """Drop a cached copy once it has been filed, or once nobody will file it."""
    try:
        _path_for(content_hash).unlink(missing_ok=True)
    except (OSError, ValueError) as exc:
        logger.debug("Could not discard cached copy %s: %s", content_hash, exc)


def prune(max_age_days: int) -> int:
    """Delete cached copies older than `max_age_days`. Returns how many went.

    The backstop for every path that never reaches approve or trash -- a document skipped
    indefinitely, a settings change that took a folder out of scope, a container replaced
    mid-review. Deleting from this cache is not the WebDAV delete CLAUDE.md rule 1 forbids:
    nothing here is the only copy of anything, and a dropped entry just means the next tick
    OCRs the document again.
    """
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    try:
        entries = list(_directory().iterdir())
    except OSError as exc:
        logger.debug("Could not read the OCR cache directory: %s", exc)
        return 0

    for entry in entries:
        try:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                entry.unlink()
                removed += 1
        except OSError as exc:
            logger.debug("Could not prune %s: %s", entry, exc)
    if removed:
        logger.info("Pruned %d cached searchable copies older than %dd.", removed, max_age_days)
    return removed
