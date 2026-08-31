"""The folder tree, folder creation, and the sibling list behind the review form.

SPEC 8.5: the tree is rooted at `allowed_root_folders` and nothing outside is reachable or
selectable. That is enforced here, not only in the picker.
"""

import logging

from app.models import AppSettings
from app.schemas import FolderContext, FolderNode, SiblingFile
from app.services import ai, naming
from app.services.errors import AppError, NotFoundError, ValidationError, WebDAVConflict
from app.services.paths import assert_within_allowed_roots, normalize_path
from app.services.times import from_storage
from app.services.webdav import WebDavEntry, WebDavService

logger = logging.getLogger(__name__)

MAX_SIBLINGS = 5


def tree(
    webdav: WebDavService, app_settings: AppSettings, path: str | None = None
) -> list[FolderNode]:
    """One level of the folder tree.

    Without a path, returns the allowed roots themselves as the top level -- the picker
    must not be able to walk up to '/' and browse the rest of the server.
    """
    roots = app_settings.allowed_root_folders
    if not roots:
        raise ValidationError("No allowed folders are configured yet — set them in Settings.")

    if path is None or path == "/":
        return [_node_for(webdav, normalize_path(root)) for root in roots]

    parent = assert_within_allowed_roots(path, roots)
    return [_node_for(webdav, entry.path) for entry in webdav.list_dirs_only(parent)]


def create(
    webdav: WebDavService, app_settings: AppSettings, parent_path: str, name: str
) -> FolderNode:
    """Create a subfolder under an allowed root (SPEC 7.2)."""
    roots = app_settings.allowed_root_folders
    if not roots:
        raise ValidationError("No allowed folders are configured yet — set them in Settings.")

    parent = assert_within_allowed_roots(parent_path, roots)
    folder_name = naming.validate_folder_name(name)
    target = normalize_path(f"{parent}/{folder_name}")

    if webdav.exists(target):
        raise WebDAVConflict(f"'{folder_name}' already exists in {parent}.")

    webdav.mkdir_p(target)
    return _node_for(webdav, target)


def context(
    webdav: WebDavService,
    app_settings: AppSettings,
    path: str,
    filename: str | None = None,
) -> FolderContext:
    """What is already in a folder, and whether a name would collide.

    SPEC 8.3 calls the sibling list "the reason the app exists": it is how the user spots a
    naming inconsistency before creating another one.
    """
    roots = app_settings.allowed_root_folders
    folder = assert_within_allowed_roots(path, roots) if roots else normalize_path(path)

    try:
        entries = webdav.list_dir(folder)
    except NotFoundError:
        # A folder the user is about to create is a normal state, not an error: the form
        # shows "will be created" rather than an error card.
        return FolderContext(
            path=folder,
            exists=False,
            siblings=[],
            total_file_count=0,
            filename_collision=False,
        )

    files = [entry for entry in entries if not entry.is_dir]
    newest = sorted(files, key=_sort_key, reverse=True)[:MAX_SIBLINGS]

    collision = False
    if filename:
        collision = any(entry.name == filename for entry in files)

    return FolderContext(
        path=folder,
        exists=True,
        siblings=[
            SiblingFile(
                filename=entry.name,
                created_at=(from_storage(entry.modified).isoformat() if entry.modified else None),
                size_bytes=entry.size_bytes or 0,
            )
            for entry in newest
        ],
        total_file_count=len(files),
        filename_collision=collision,
    )


def _sort_key(entry: WebDavEntry) -> float:
    return entry.modified.timestamp() if entry.modified else 0.0


def _node_for(webdav: WebDavService, path: str) -> FolderNode:
    """Build a node, peeking inside for its child and file counts.

    This costs one listing per node, so rendering a level with N subfolders is N+1
    requests. The 30-second listing cache absorbs repeat views, and the picker is lazy, so
    only the level actually opened pays it.
    """
    normalized = normalize_path(path)
    try:
        entries = webdav.list_dir(normalized)
        has_children = any(entry.is_dir for entry in entries)
        file_count = sum(1 for entry in entries if not entry.is_dir)
    except NotFoundError:
        has_children, file_count = False, 0

    return FolderNode(
        path=normalized,
        name=normalized.rsplit("/", 1)[-1] or "/",
        has_children=has_children,
        children=None,
        file_count=file_count,
    )


def prompt_context(
    service: WebDavService, app_settings: AppSettings
) -> tuple[list[str], list[str]]:
    """The folder tree and a sample of existing filenames, for SPEC 6.3's prompt.

    Descends rather than listing only the roots: SPEC 6.3 wants filenames "sampled from
    across those folders", and a setup that files everything into a subfolder (say
    /Archive/2026) has no files at the root at all -- sampling only there would send the
    model an empty list and lose the naming convention entirely.
    """
    tree: list[str] = []
    samples: list[str] = []

    for root in app_settings.allowed_root_folders:
        tree.append(root)
        frontier = [(root, 0)]
        while frontier:
            path, depth = frontier.pop(0)
            if depth >= ai.MAX_TREE_DEPTH:
                continue
            try:
                entries = service.list_dir(path)
            except AppError as exc:
                logger.debug("Could not list %s for prompt context: %s", path, exc.message)
                continue
            for entry in entries:
                if entry.is_dir:
                    tree.append(entry.path)
                    frontier.append((entry.path, depth + 1))
                elif len(samples) < ai.MAX_SAMPLE_FILENAMES:
                    samples.append(entry.name)

    return tree, samples
