"""Tests for the path security boundary.

The `..` cases are not hypothetical: handed to webdav4, `Documents/../../../../etc/passwd`
against a base URL of `https://host/remote.php/dav/files/jonas` resolves to
`https://host/remote.php/etc/passwd` -- out of the user's home entirely.
"""

import pytest

from app.services.errors import OutsideAllowedRootsError
from app.services.paths import (
    assert_within_allowed_roots,
    is_within,
    normalize_path,
)

ROOTS = ["/Documents", "/Archive"]


class TestNormalizePathAccepts:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("/Documents", "/Documents"),
            ("/Documents/", "/Documents"),
            ("/Documents//Finance", "/Documents/Finance"),
            ("/Documents/./Finance", "/Documents/Finance"),
            ("  /Documents/Finance  ", "/Documents/Finance"),
            ("/", "/"),
            # Legitimate names that must survive the percent-decode check.
            ("/Documents/100% Complete", "/Documents/100% Complete"),
            ("/Documents/50%2E%2E", "/Documents/50%2E%2E"),
            # Unicode is passed through untouched -- Nextcloud stores the bytes it is given,
            # so NFC-folding an NFD name here would break lookups for files that exist.
            ("/Documents/café.pdf", "/Documents/café.pdf"),
            ("/Documents/日本語", "/Documents/日本語"),
            ("/Documents/emoji-👍🏽.pdf", "/Documents/emoji-👍🏽.pdf"),
        ],
    )
    def test_normalizes(self, raw: str, expected: str) -> None:
        assert normalize_path(raw) == expected

    def test_preserves_unicode_composition(self) -> None:
        nfd = "/Documents/cafe\u0301.pdf"
        assert normalize_path(nfd) == nfd


class TestNormalizePathRejects:
    @pytest.mark.parametrize(
        "raw",
        [
            "/Documents/../etc/passwd",
            "/../etc/passwd",
            "/Documents/../../root",
            "/Documents/subdir/../../../etc",
            "..",
            "/..",
        ],
        ids=lambda p: f"dotdot:{p}",
    )
    def test_rejects_parent_traversal(self, raw: str) -> None:
        with pytest.raises(ValueError, match=r"\.\."):
            normalize_path(raw)

    @pytest.mark.parametrize(
        "raw",
        [
            "/Documents/%2e%2e/secret",
            "/Documents/%2E%2E/secret",
            # Double-encoded: one decode round yields %2e%2e, a second yields '..'.
            "/Documents/%252e%252e/secret",
        ],
        ids=["encoded", "encoded-upper", "double-encoded"],
    )
    def test_rejects_percent_encoded_traversal(self, raw: str) -> None:
        with pytest.raises(ValueError, match="percent-decoding"):
            normalize_path(raw)

    def test_rejects_percent_encoded_separator(self) -> None:
        with pytest.raises(ValueError, match="percent-decoding"):
            normalize_path("/Documents/a%2Fb%2F..%2Fescape")

    def test_rejects_fullwidth_traversal(self) -> None:
        # NFKC folds U+FF0E into '.', turning a fullwidth '..' into a real parent reference.
        with pytest.raises(ValueError, match="Unicode normalization"):
            normalize_path("/Documents/\uff0e\uff0e/secret")

    def test_rejects_fullwidth_separator(self) -> None:
        # NFKC folds U+FF0F into '/'.
        with pytest.raises(ValueError, match="Unicode normalization"):
            normalize_path("/Documents/a\uff0f\uff0e\uff0e\uff0fescape")

    @pytest.mark.parametrize(
        "raw",
        ["Documents/x", "relative", "./relative", "C:/Windows"],
        ids=lambda p: f"relative:{p}",
    )
    def test_rejects_relative_paths(self, raw: str) -> None:
        with pytest.raises(ValueError, match="absolute"):
            normalize_path(raw)

    def test_rejects_null_byte(self) -> None:
        with pytest.raises(ValueError, match="null"):
            normalize_path("/Documents/a\x00.pdf")

    @pytest.mark.parametrize("char", ["\n", "\r", "\t", "\x1b", "\x7f"])
    def test_rejects_control_characters(self, char: str) -> None:
        with pytest.raises(ValueError, match="control"):
            normalize_path(f"/Documents/a{char}b")

    def test_rejects_backslash(self) -> None:
        with pytest.raises(ValueError, match="backslash"):
            normalize_path("/Documents\\..\\..\\etc")

    @pytest.mark.parametrize("raw", ["", "   "])
    def test_rejects_empty(self, raw: str) -> None:
        with pytest.raises(ValueError, match="empty"):
            normalize_path(raw)


class TestIsWithin:
    def test_matches_whole_segments_only(self) -> None:
        # The classic prefix bug: /Documents must not appear to contain /DocumentsSecret.
        assert is_within("/Documents", "/Documents/Finance") is True
        assert is_within("/Documents", "/Documents") is True
        assert is_within("/Documents", "/DocumentsSecret") is False
        assert is_within("/Documents", "/Documents2/x") is False

    def test_root_contains_everything(self) -> None:
        assert is_within("/", "/anything/at/all") is True


class TestAssertWithinAllowedRoots:
    def test_accepts_path_inside_a_root(self) -> None:
        assert assert_within_allowed_roots("/Documents/Finance/2026", ROOTS) == (
            "/Documents/Finance/2026"
        )

    def test_accepts_a_root_itself(self) -> None:
        assert assert_within_allowed_roots("/Archive", ROOTS) == "/Archive"

    def test_normalizes_what_it_returns(self) -> None:
        # Callers use the return value, so they cannot keep using the raw string.
        assert assert_within_allowed_roots("/Documents//Finance/", ROOTS) == "/Documents/Finance"

    @pytest.mark.parametrize(
        "raw",
        ["/etc/passwd", "/", "/DocumentsSecret/x", "/Documents2", "/home/jonas"],
        ids=lambda p: f"outside:{p}",
    )
    def test_rejects_paths_outside_every_root(self, raw: str) -> None:
        with pytest.raises(OutsideAllowedRootsError):
            assert_within_allowed_roots(raw, ROOTS)

    def test_traversal_that_would_land_inside_a_root_is_still_rejected(self) -> None:
        # Rejected as a malformed path before the root check ever runs.
        with pytest.raises(ValueError):
            assert_within_allowed_roots("/Documents/../Documents/ok", ROOTS)

    def test_extra_allowed_covers_the_trash_folder(self) -> None:
        assert assert_within_allowed_roots("/Trash/x", ROOTS, extra_allowed=["/Trash"]) == (
            "/Trash/x"
        )

    def test_trash_folder_is_not_allowed_without_the_exception(self) -> None:
        with pytest.raises(OutsideAllowedRootsError):
            assert_within_allowed_roots("/Trash/x", ROOTS)
