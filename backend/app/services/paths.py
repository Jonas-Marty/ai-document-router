"""Path normalization and the allowed-roots security boundary.

This module is the single place where user-supplied paths are made safe. It is deliberately
paranoid: `..` in a path handed to webdav4 escapes the base URL's own path prefix entirely
(verified: `Documents/../../../../etc/passwd` against a base of
`https://host/remote.php/dav/files/jonas` resolves to `https://host/remote.php/etc/passwd`),
so these checks are the boundary, not hygiene.
"""

import unicodedata
from collections.abc import Iterable
from urllib.parse import unquote

from app.services.errors import OutsideAllowedRootsError

# Percent-decoding is applied repeatedly until it stabilises, so `%252e%252e` is caught as
# well as `%2e%2e`. A handful of rounds is far more than any real server applies.
_MAX_DECODE_ROUNDS = 5


def normalize_path(path: str) -> str:
    """Normalize an absolute path: leading '/', no trailing slash, no '.' or '..' segments.

    Raises ValueError for anything that could escape a parent directory, including paths
    where percent-decoding or Unicode compatibility folding would *introduce* a traversal.
    """
    if "\x00" in path:
        raise ValueError("Path must not contain null bytes.")

    stripped = path.strip()
    if not stripped:
        raise ValueError("Path must not be empty.")

    if any(unicodedata.category(char) == "Cc" for char in stripped):
        raise ValueError("Path must not contain control characters.")

    # Backslash is not a separator on a WebDAV server, but intermediaries have been known to
    # fold it into one. SPEC 7.1 forbids it in filenames anyway.
    if "\\" in stripped:
        raise ValueError("Path must not contain backslashes.")

    if not stripped.startswith("/"):
        raise ValueError(f"Path '{path}' must be absolute.")

    _reject_smuggled_traversal(stripped)

    segments = [segment for segment in stripped.split("/") if segment and segment != "."]
    if ".." in segments:
        raise ValueError(f"Path '{path}' must not contain '..' segments.")

    return "/" + "/".join(segments)


def _reject_smuggled_traversal(path: str) -> None:
    """Reject paths where decoding or Unicode folding would create a traversal.

    The path itself is never rewritten -- Nextcloud stores the bytes it is given, so
    normalizing (e.g. NFC-folding an NFD filename) would break lookups for paths that
    legitimately exist. These are checks, not transforms.
    """
    baseline_slashes = path.count("/")

    # A server that percent-decodes before normalizing turns '%2e%2e' back into '..'.
    # Only flag when decoding *introduces* a traversal, so '100% Complete' stays legal.
    current = path
    for _ in range(_MAX_DECODE_ROUNDS):
        decoded = unquote(current)
        if decoded == current:
            break
        current = decoded
        _reject_if_traversal_appeared(current, baseline_slashes, "percent-decoding")

    # NFKC folds fullwidth forms into real separators: U+FF0F -> '/' and U+FF0E -> '.',
    # so a fullwidth '..' becomes a genuine parent reference downstream.
    folded = unicodedata.normalize("NFKC", path)
    if folded != path:
        _reject_if_traversal_appeared(folded, baseline_slashes, "Unicode normalization")


def _reject_if_traversal_appeared(candidate: str, baseline_slashes: int, how: str) -> None:
    if ".." in [segment for segment in candidate.split("/") if segment]:
        raise ValueError(f"Path must not contain '..' segments after {how}.")
    if candidate.count("/") > baseline_slashes:
        raise ValueError(f"Path must not contain separators introduced by {how}.")


def is_within(root: str, path: str) -> bool:
    """True if `path` is `root` itself or nested inside it. Both must already be normalized.

    Compares whole segments, so '/Documents' does not contain '/DocumentsSecret'.
    """
    if root == "/":
        return True
    return path == root or path.startswith(root + "/")


def assert_within_allowed_roots(
    path: str, allowed_roots: Iterable[str], extra_allowed: Iterable[str] = ()
) -> str:
    """Normalize `path` and confirm it sits inside one of the permitted trees.

    `extra_allowed` carries the deliberate exceptions -- the trash folder (SPEC 6.1) and the
    watch folder -- which are configuration, not user input. Returns the normalized path so
    callers cannot accidentally keep using the raw one.
    """
    normalized = normalize_path(path)
    permitted = [normalize_path(root) for root in (*allowed_roots, *extra_allowed)]
    if not any(is_within(root, normalized) for root in permitted):
        raise OutsideAllowedRootsError(f"'{normalized}' is outside the allowed folders.")
    return normalized
