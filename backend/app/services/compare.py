"""Run several ways of reading a document side by side, so a person can judge them.

The app reads a document one way per tick, and gives no way to tell whether a different
reading of the same scan would have done better. This runs the alternatives against one
document and hands back what each produced, without changing anything: nothing here writes
a proposal, and the review form is still where a filename is chosen.

Every method files through the filing chain, so what is being compared is the *reading* --
flat text layer, Tesseract, a vision model's markdown -- with the model that chooses the
filename held constant.

Deliberately synchronous and on demand. Every method costs an LLM call, so this is a thing
someone asks for while looking at a document, never something the poller does on a tick.
"""

import logging
import time
from dataclasses import dataclass

from sqlmodel import Session

from app.models import AiTask, AppSettings, Document
from app.services import ai, ai_tasks, extraction, folders, ocr, transcribe
from app.services.errors import AppError
from app.services.webdav import WebDavService

logger = logging.getLogger(__name__)

TEXT_LAYER = "text_layer"
OCR = "ocr"
MARKDOWN = "markdown"

# The text handed to the model is capped the same way the production path caps it, so a
# method is not flattered by being allowed a longer prompt than the real one gets.
MAX_TEXT_CHARS = extraction.MAX_TEXT_CHARS


@dataclass(frozen=True)
class MethodResult:
    """What one way of reading the document produced."""

    method: str
    model_name: str
    label: str
    text_preview: str
    proposal: ai.Proposal | None
    error: str | None
    duration_ms: int


def compare(
    session: Session, webdav: WebDavService, app_settings: AppSettings, document: Document
) -> list[MethodResult]:
    """Read one document every configured way and return each result in turn.

    A method that fails is a result too, not an omission: "the vision model refused this
    image" is exactly the finding someone comparing methods needs to see, and dropping it
    would leave them wondering whether it had been tried.
    """
    data = b"".join(webdav.read_stream(document.webdav_path))
    filing = ai_tasks.resolve_chain(session, AiTask.filing)
    extracting = ai_tasks.resolve_chain(session, AiTask.extraction)
    filing_label = filing[0].label if filing else "no filing endpoint"
    folder_tree, sample_filenames = folders.prompt_context(webdav, app_settings)

    results: list[MethodResult] = []

    def propose(method: str, label: str, text: str) -> None:
        started = time.monotonic()
        prompt = ai.build_prompt(
            text, folder_tree, sample_filenames, app_settings.filename_pattern_hint
        )
        proposal: ai.Proposal | None = None
        error: str | None = None
        try:
            proposal = ai_tasks.run_chain(
                filing,
                lambda step: ai.request_proposal(
                    endpoint_url=step.endpoint_url,
                    model_name=step.model_name,
                    api_key=step.api_key,
                    prompt=prompt,
                    allowed_roots=list(app_settings.allowed_root_folders),
                ),
                AiTask.filing,
            )
        except ai.ProposalRejected as exc:
            error = exc.reason
        except AppError as exc:
            error = exc.message

        results.append(
            MethodResult(
                method=method,
                model_name=filing_label,
                label=label,
                text_preview=text[:MAX_TEXT_CHARS],
                proposal=proposal,
                error=error,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        )

    extracted = extraction.extract(data, document.original_filename, document.mime_type)

    # 1. The PDF's own text layer, flattened by pypdf.
    if extracted.has_usable_text:
        propose(TEXT_LAYER, "Text layer", extracted.text)
    else:
        results.append(
            _skipped(
                TEXT_LAYER,
                filing_label,
                "Text layer",
                extracted.text_error or extraction.NO_TEXT_LAYER_MESSAGE,
            )
        )

    pages, render_error = _render(data, extracted.mime_type)

    # 2. Classical OCR over the rendered pages.
    if render_error is not None:
        results.append(_skipped(OCR, filing_label, "Tesseract OCR", render_error))
    else:
        try:
            text = ocr.read_pages(pages)
        except ocr.OcrUnavailable as exc:
            results.append(_skipped(OCR, filing_label, "Tesseract OCR", str(exc)))
        else:
            if text:
                propose(OCR, "Tesseract OCR", text)
            else:
                results.append(
                    _skipped(
                        OCR,
                        filing_label,
                        "Tesseract OCR",
                        "Tesseract read no text off these pages.",
                    )
                )

    # 3. The production two-stage path: a vision model transcribes, the filing chain reads
    #    that markdown. This is the one that tells you whether stage one is earning its keep.
    label = "Markdown extraction"
    if not extracting:
        results.append(
            _skipped(
                MARKDOWN,
                filing_label,
                label,
                "No endpoint is assigned to the extraction task.",
            )
        )
    else:
        label = f"Markdown · {extracting[0].label}"
        try:
            transcription = transcribe.transcribe(data, extracting)
        except (AppError, extraction.RenderUnavailable) as exc:
            reason = exc.message if isinstance(exc, AppError) else str(exc)
            results.append(_skipped(MARKDOWN, filing_label, label, reason))
        else:
            propose(MARKDOWN, label, transcription.markdown)

    return results


def _render(data: bytes, mime_type: str) -> tuple[list[bytes], str | None]:
    if mime_type != extraction.PDF_MIME:
        return [], "Only PDFs can be rendered to pages here."
    try:
        return extraction.render_pages(data), None
    except extraction.RenderUnavailable as exc:
        return [], str(exc)


def _skipped(method: str, model_name: str, label: str, reason: str) -> MethodResult:
    """A method that never got as far as an LLM call. Reported, not omitted."""
    return MethodResult(
        method=method,
        model_name=model_name,
        label=label,
        text_preview="",
        proposal=None,
        error=reason,
        duration_ms=0,
    )
