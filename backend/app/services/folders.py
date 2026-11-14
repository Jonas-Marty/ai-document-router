"""The folder tree, folder creation, and the sibling list behind the review form.

SPEC 8.5: the tree is rooted at `allowed_root_folders` and nothing outside is reachable or
selectable. That is enforced here, not only in the picker.
"""

import logging
import os
import re
from datetime import UTC, datetime

from app.models import AppSettings
from app.schemas import FolderContext, FolderNode, SiblingFile
from app.services import ai, naming
from app.services.errors import AppError, NotFoundError, ValidationError, WebDAVConflict
from app.services.paths import assert_within_allowed_roots, is_within, normalize_path
from app.services.times import from_storage
from app.services.webdav import WebDavEntry, WebDavService

logger = logging.getLogger(__name__)

MAX_SIBLINGS = 5
# "+N more" in the picker loads the rest; a bound so one huge folder stays one response.
MAX_ALL_SIBLINGS = 2000
# The picker's file panel shows this many of a folder's newest files, then "+N more".
MAX_RECENT_FILES = 20


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


def browse(webdav: WebDavService, path: str | None = None) -> list[FolderNode]:
    """One level of the user's whole WebDAV store, for choosing folders in Settings.

    Unlike tree() this is not confined to the allowed roots: it is how those roots get
    picked in the first place. It only ever reads the user's own server with their own
    credentials, which can already reach any path typed into Settings.
    """
    try:
        parent = normalize_path(path or "/")
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
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
    *,
    every_file: bool = False,
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
    limit = MAX_ALL_SIBLINGS if every_file else MAX_SIBLINGS
    newest = sorted(files, key=_sort_key, reverse=True)[:limit]

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
    """Build a node, peeking inside for its child count and newest files.

    This costs one listing per node, so rendering a level with N subfolders is N+1
    requests. The 30-second listing cache absorbs repeat views, and the picker is lazy, so
    only the level actually opened pays it.
    """
    normalized = normalize_path(path)
    try:
        entries = webdav.list_dir(normalized)
        has_children = any(entry.is_dir for entry in entries)
        files = [entry for entry in entries if not entry.is_dir]
    except NotFoundError:
        has_children, files = False, []
    newest = sorted(files, key=_sort_key, reverse=True)[:MAX_RECENT_FILES]

    return FolderNode(
        path=normalized,
        name=normalized.rsplit("/", 1)[-1] or "/",
        has_children=has_children,
        children=None,
        file_count=len(files),
        recent_files=[entry.name for entry in newest],
    )


def prompt_context(
    service: WebDavService, app_settings: AppSettings
) -> tuple[list[str], list[str]]:
    """The folder tree and a sample of existing filenames, for SPEC 6.3's prompt.

    Descends rather than listing only the roots: SPEC 6.3 wants filenames "sampled from
    across those folders", and a setup that files everything into a subfolder (say
    /Archive/2026) has no files at the root at all -- sampling only there would send the
    model an empty list and lose the naming convention entirely.

    The watch folder and the trash are skipped wherever they are -- inside an allowed root or
    listed as one: their files are unsorted scans, the opposite of an example, and neither is
    a destination.
    Samples are the most recently modified files, because the convention drifts and the
    newest names show the one in use now.
    """
    excluded = [
        normalize_path(path)
        for path in (app_settings.watch_folder_path, app_settings.trash_folder_path)
        if path
    ]
    tree: list[str] = []
    files: list[WebDavEntry] = []
    pattern = _compile_pattern(app_settings.filename_pattern)

    for root in app_settings.allowed_root_folders:
        # Settings refuses a root that is or sits in the watch folder or trash, but data
        # saved before that check (or seeded by a migration) can still have one.
        if any(is_within(skip, normalize_path(root)) for skip in excluded):
            continue
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
                    if any(is_within(skip, normalize_path(entry.path)) for skip in excluded):
                        continue
                    tree.append(entry.path)
                    frontier.append((entry.path, depth + 1))
                else:
                    files.append(entry)

    newest_first = sorted(
        files, key=lambda entry: entry.modified or datetime.min.replace(tzinfo=UTC), reverse=True
    )
    matching = [entry.name for entry in newest_first if _fits(entry.name, pattern)]
    samples = matching[: ai.MAX_SAMPLE_FILENAMES]
    return tree, samples


def _compile_pattern(pattern: str | None) -> re.Pattern[str] | None:
    if not pattern:
        return None
    try:
        return re.compile(pattern)
    except re.error:
        return None


def _fits(filename: str, pattern: re.Pattern[str] | None) -> bool:
    """Whether a file is an example worth showing the model.

    With a pattern, only names that follow it: the model copies examples more readily than it
    follows a written rule, so one old "2026 Steuern.pdf" teaches it to write year-only names
    however clearly the pattern asks for a full date. Matched without the extension, as the
    review form matches the name field.
    """
    return pattern is None or pattern.search(os.path.splitext(filename)[0]) is not None
