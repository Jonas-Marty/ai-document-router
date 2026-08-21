def normalize_path(path: str) -> str:
    """Normalize an absolute path: leading '/', no trailing slash, no '..' segments.

    Shared by settings validation and the WebDAV service — every boundary that accepts a
    user-supplied path runs it through here first.
    """
    stripped = path.strip()
    if not stripped.startswith("/"):
        raise ValueError(f"Path '{path}' must be absolute.")
    segments = [segment for segment in stripped.split("/") if segment]
    if any(segment == ".." for segment in segments):
        raise ValueError(f"Path '{path}' must not contain '..' segments.")
    return "/" + "/".join(segments)


def is_within(root: str, path: str) -> bool:
    """True if `path` is `root` itself or nested inside it. Both must already be normalized."""
    if root == "/":
        return True
    return path == root or path.startswith(root + "/")
