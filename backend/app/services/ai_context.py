"""What each AI task is sent, assembled from the current settings, for the Settings screen.

The review screen already shows what was sent for one proposal. What it cannot show is what
*will* be sent: after editing the naming hint, the instructions, or the allowed folders,
nobody can check the result without waiting for a scan. This builds the messages the same
way the poller does, with the one per-document part -- the document itself -- left as a
placeholder.

Its own module rather than a function in ai_tasks, because it needs transcribe's prompts and
transcribe already imports ai_tasks.
"""

from dataclasses import dataclass

from sqlmodel import Session

from app.models import AiTask, AppSettings
from app.services import ai, ai_tasks, extraction, folders, transcribe
from app.services.webdav import WebDavService

DOCUMENT_TEXT_PLACEHOLDER = (
    "[The document's text goes here: the extraction task's markdown, or the PDF's own text "
    f"layer when extraction is unavailable. First {extraction.MAX_TEXT_CHARS} characters.]"
)


@dataclass(frozen=True)
class TaskContext:
    task: AiTask
    chain: list[str]
    system_prompt: str
    user_prompt: str
    # What travels with the message that the message itself does not say.
    note: str


def preview(
    session: Session, webdav: WebDavService, app_settings: AppSettings
) -> list[TaskContext]:
    """Both tasks in the order they run. Walks WebDAV for the folder tree, as a tick does."""
    tree, samples = folders.prompt_context(webdav, app_settings)
    return [
        TaskContext(
            task=AiTask.extraction,
            chain=_labels(session, app_settings.user_id, AiTask.extraction),
            system_prompt=transcribe.SYSTEM_PROMPT,
            user_prompt=transcribe.user_prompt(app_settings.extraction_instructions),
            note=(
                f"Sent with the first {extraction.MAX_RENDERED_PAGES} pages of the document as "
                f"images, {transcribe.PAGES_PER_REQUEST} per request."
            ),
        ),
        TaskContext(
            task=AiTask.filing,
            chain=_labels(session, app_settings.user_id, AiTask.filing),
            system_prompt=ai.SYSTEM_PROMPT,
            user_prompt=ai.build_prompt(
                DOCUMENT_TEXT_PLACEHOLDER,
                tree,
                samples,
                app_settings.filename_pattern_hint,
                app_settings.filing_instructions,
            ),
            note=(
                "The folder tree and example filenames are read from WebDAV when each "
                "document is filed, so they follow your folders as they change."
            ),
        ),
    ]


def _labels(session: Session, user_id: str, task: AiTask) -> list[str]:
    return [
        f"{step.endpoint_name} · {step.model_name}"
        for step in ai_tasks.chain_for(session, user_id, task)
    ]
