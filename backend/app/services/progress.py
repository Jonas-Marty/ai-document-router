"""What the analysis of a document is doing right now, for the review screen.

In memory, not in the database: it is only true while the poller is working on the
document, which happens in this process, and writing it to SQLite from inside the
poller's transaction would contend with that very transaction. Lost on restart, which is
right -- a restarted process is not analysing anything.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_current: ContextVar[str | None] = ContextVar("analysed_document", default=None)
_stages: dict[str, str] = {}
_lock = threading.Lock()


@contextmanager
def tracking(document_id: str) -> Iterator[None]:
    """Attribute report() calls in this block to `document_id`; clear them after."""
    token = _current.set(document_id)
    try:
        yield
    finally:
        _current.reset(token)
        with _lock:
            _stages.pop(document_id, None)


def report(stage: str) -> None:
    """Say what is happening now. A no-op outside tracking(), e.g. in a comparison."""
    document_id = _current.get()
    if document_id is None:
        return
    with _lock:
        _stages[document_id] = stage


def stage_of(document_id: str) -> str | None:
    with _lock:
        return _stages.get(document_id)
