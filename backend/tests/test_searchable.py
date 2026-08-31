"""Tests for producing and caching a searchable copy.

ocrmypdf is never actually run here: it needs ghostscript and tesseract present and takes
tens of seconds on a real scan, neither of which belongs in a unit suite. What is tested is
everything around the subprocess -- how its outcomes are translated, and a cache that
approve depends on to hand back exactly the bytes the poller put in it.
"""

import subprocess
import time
from pathlib import Path

import pytest

from app.config import settings as config
from app.services import searchable

HASH = "a" * 64
OTHER_HASH = "b" * 64


class _Completed:
    def __init__(self, returncode: int, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stdout = b""
        self.stderr = stderr


def _fake_run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    returncode: int = 0,
    stderr: bytes = b"",
    output: bytes | None = b"%PDF-1.7 searchable",
    raises: Exception | None = None,
) -> list[list[str]]:
    """Stand in for the ocrmypdf subprocess, writing the output file it would have written."""
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> _Completed:
        calls.append(command)
        if raises is not None:
            raise raises
        if output is not None:
            Path(command[-1]).write_bytes(output)
        return _Completed(returncode, stderr)

    monkeypatch.setattr(searchable.shutil, "which", lambda _: "/usr/bin/ocrmypdf")
    monkeypatch.setattr(searchable.subprocess, "run", run)
    return calls


class TestBuild:
    def test_returns_the_pdf_ocrmypdf_wrote(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _fake_run(monkeypatch)

        assert searchable.build(b"%PDF-1.7 scan") == b"%PDF-1.7 searchable"

    def test_never_re_encodes_the_page_images(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The flags are the whole safety argument for filing this over someone's scan.

        --output-type pdf grafts a text layer onto the original pages instead of rewriting
        the file through ghostscript, and --optimize 0 keeps pngquant and friends away from
        them. A change to either belongs in a conversation, not in a passing test.
        """
        calls = _fake_run(monkeypatch)

        searchable.build(b"%PDF-1.7 scan")

        command = calls[0]
        assert command[0] == "ocrmypdf"
        assert "--output-type" in command
        assert command[command.index("--output-type") + 1] == "pdf"
        assert "--optimize" in command
        assert command[command.index("--optimize") + 1] == "0"
        assert "--skip-text" in command
        assert command[command.index("-l") + 1] == "deu+eng"

    def test_a_missing_binary_is_reported_not_raised_as_oserror(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(searchable.shutil, "which", lambda _: None)

        with pytest.raises(searchable.SearchableUnavailable, match="isn't installed"):
            searchable.build(b"%PDF-1.7 scan")

    def test_a_failure_carries_the_last_line_of_stderr(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _fake_run(monkeypatch, returncode=1, stderr=b"warning: something\nInputFileError: bad")

        with pytest.raises(searchable.SearchableUnavailable, match="InputFileError: bad"):
            searchable.build(b"not a pdf")

    def test_an_encrypted_pdf_says_so(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ocrmypdf's own exit code, turned into something worth showing someone."""
        _fake_run(monkeypatch, returncode=4)

        with pytest.raises(searchable.SearchableUnavailable, match="encrypted"):
            searchable.build(b"%PDF-1.7 scan")

    def test_a_timeout_becomes_a_readable_reason(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _fake_run(monkeypatch, raises=subprocess.TimeoutExpired("ocrmypdf", 600.0))

        with pytest.raises(searchable.SearchableUnavailable, match="longer than"):
            searchable.build(b"%PDF-1.7 scan")

    def test_a_zero_exit_with_no_output_file_is_still_a_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Otherwise approve would upload b"" over a perfectly good document."""
        _fake_run(monkeypatch, output=None)

        with pytest.raises(searchable.SearchableUnavailable, match="no output"):
            searchable.build(b"%PDF-1.7 scan")


class TestCache:
    def test_stores_and_loads_the_same_bytes(self) -> None:
        searchable.store(HASH, b"%PDF-1.7 searchable")

        assert searchable.load(HASH) == b"%PDF-1.7 searchable"

    def test_a_hash_nothing_was_stored_for_loads_as_none(self) -> None:
        assert searchable.load(OTHER_HASH) is None

    def test_leaves_no_partial_file_behind(self) -> None:
        """approve uploads whatever is in this directory, so a half-written file here would
        become a truncated document on the server."""
        searchable.store(HASH, b"%PDF-1.7 searchable")

        names = sorted(entry.name for entry in Path(config.ocr_cache_dir).iterdir())
        assert names == [f"{HASH}.pdf"]

    def test_discard_removes_it_and_is_safe_to_repeat(self) -> None:
        searchable.store(HASH, b"bytes")
        searchable.discard(HASH)
        searchable.discard(HASH)

        assert searchable.load(HASH) is None

    def test_a_hash_that_is_not_hex_cannot_name_a_file(self) -> None:
        """The key comes out of the database, and `../../etc/passwd` as a filename is the
        difference between a cache and an arbitrary write."""
        with pytest.raises(ValueError, match="Not a content hash"):
            searchable.store("../../escape", b"bytes")

        # load and discard swallow it instead, because both are called on paths where a
        # bad key means "there is nothing here", not "stop what you are doing".
        assert searchable.load("../../escape") is None
        searchable.discard("../../escape")

    def test_prune_drops_the_old_and_keeps_the_recent(self) -> None:
        searchable.store(HASH, b"old")
        searchable.store(OTHER_HASH, b"new")
        stale = Path(config.ocr_cache_dir) / f"{HASH}.pdf"
        long_ago = time.time() - 30 * 86400
        import os

        os.utime(stale, (long_ago, long_ago))

        assert searchable.prune(max_age_days=14) == 1
        assert searchable.load(HASH) is None
        assert searchable.load(OTHER_HASH) == b"new"

    def test_prune_on_a_directory_that_does_not_exist_yet_is_a_no_op(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The first tick after a deploy runs before anything has ever been cached."""
        monkeypatch.setattr(config, "ocr_cache_dir", str(tmp_path / "never-created"))

        assert searchable.prune(max_age_days=14) == 0
