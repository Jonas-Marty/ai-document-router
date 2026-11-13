"""Run several ways of reading a document side by side, so a person can judge them.

The app reads a document one way per tick, and gives no way to tell whether a different
reading of the same scan would have done better. This runs the alternatives against one
document and hands back what each produced, without changing anything: nothing here writes
a proposal, and the review form is still where a filename is chosen.

Every method files through the filing step of the document's configuration, so what is
being compared is the *reading* -- flat text layer, Tesseract, a vision model's markdown --
with the model that chooses the filename held constant.

Deliberately synchronous and on demand. Every method costs an LLM call, so this is a thing
someone asks for while looking at a document, never something the poller does on a tick.
"""

import logging
import time
from dataclasses import dataclass

from sqlmodel import Session

from app.models import AppSettings, AuditAction, Document, ReadingMethod
from app.services import (
    ai,
    ai_usage,
    analysis,
    audit,
    configurations,
    extraction,
    folders,
    ocr,
    transcribe,
)
from app.services.audit import Actor
from app.services.errors import AppError, ValidationError
from app.services.webdav import WebDavService

logger = logging.getLogger(__name__)

TEXT_LAYER = "text_layer"
OCR = "ocr"
MARKDOWN = "markdown"


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
    # What this method cost, so the comparison is of price as well as quality. The markdown
    # method's includes the transcription it filed from.
    usage: ai.UsageSummary | None = None


def compare(
    session: Session,
    webdav: WebDavService,
    app_settings: AppSettings,
    document: Document,
    *,
    actor: Actor,
) -> list[MethodResult]:
    """Read one document every configured way and return each result in turn.

    A method that fails is a result too, not an omission: "the vision model refused this
    image" is exactly the finding someone comparing methods needs to see, and dropping it
    would leave them wondering whether it had been tried.
    """
    configuration = configurations.for_document(session, document)
    if configuration is None:
        raise ValidationError("There is no configuration yet — create one in the Lab.")
    plan = configurations.plan(session, configuration)
    data = b"".join(webdav.read_stream(document.webdav_path))
    filing_label = plan.filing_step.label if plan.filing_step else "no filing connection"
    context = folders.prompt_context(webdav, app_settings)

    results: list[MethodResult] = []
    meters: list[ai.UsageMeter] = []

    def propose(method: str, label: str, text: str, meter: ai.UsageMeter | None = None) -> None:
        meter = meter or ai.UsageMeter()
        if meter not in meters:
            meters.append(meter)
        started = time.monotonic()
        proposal: ai.Proposal | None = None
        error: str | None = None
        try:
            proposal = analysis.file_text(plan, text, context, app_settings, meter).proposal
        except ai.ProposalRejected as exc:
            error = exc.reason
        except AppError as exc:
            error = exc.message

        results.append(
            MethodResult(
                method=method,
                model_name=filing_label,
                label=label,
                # Capped as the filing prompt caps it, so a method is not flattered by being
                # shown more than the model was given.
                text_preview=text[: plan.max_text_chars],
                proposal=proposal,
                error=error,
                duration_ms=int((time.monotonic() - started) * 1000),
                usage=meter.summary(),
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

    # 3. The configuration's vision reading, filed the same way. This is the one that tells
    #    you whether a vision model is earning its keep.
    label = "Vision reading"
    step = plan.reading_step
    if plan.reading_method != ReadingMethod.vision or step is None:
        results.append(
            _skipped(
                MARKDOWN,
                filing_label,
                label,
                f"The configuration '{plan.configuration_name}' doesn't read with a model.",
            )
        )
    else:
        label = f"Markdown · {step.label}"
        meter = ai.UsageMeter()
        meters.append(meter)
        try:
            transcription = transcribe.transcribe(
                data,
                step,
                system_prompt=plan.reading_system_prompt,
                instructions=plan.reading_instructions,
                parameters=plan.reading_parameters,
                meter=meter,
            )
        except (AppError, extraction.RenderUnavailable) as exc:
            reason = exc.message if isinstance(exc, AppError) else str(exc)
            results.append(_skipped(MARKDOWN, filing_label, label, reason, meter.summary()))
        else:
            propose(MARKDOWN, label, transcription.markdown, meter)

    _audit(session, document, results, actor, meters)
    return results


def _audit(
    session: Session,
    document: Document,
    results: list[MethodResult],
    actor: Actor,
    meters: list[ai.UsageMeter],
) -> None:
    """Nothing else here is stored, but what the comparison spent is: it is real money."""
    audit.record(
        session,
        AuditAction.document_compared,
        actor=actor,
        document_id=document.id,
        summary=f"Compared {len(results)} ways of reading {document.original_filename}",
        details={
            "methods": [
                {
                    "method": result.method,
                    "label": result.label,
                    "outcome": "proposed" if result.proposal else "failed",
                    "error": result.error,
                    "duration_ms": result.duration_ms,
                    **audit.usage_details(result.usage),
                }
                for result in results
            ]
        },
        usage=[result.usage for result in results],
    )
    ai_usage.store(
        session, meters, user_id=document.user_id, document_id=document.id, comparison=True
    )
    session.commit()


def _render(data: bytes, mime_type: str) -> tuple[list[bytes], str | None]:
    if mime_type != extraction.PDF_MIME:
        return [], "Only PDFs can be rendered to pages here."
    try:
        return extraction.render_pages(data), None
    except extraction.RenderUnavailable as exc:
        return [], str(exc)


def _skipped(
    method: str,
    model_name: str,
    label: str,
    reason: str,
    usage: ai.UsageSummary | None = None,
) -> MethodResult:
    """A method that never got as far as filing. Reported, not omitted.

    `usage` is for the markdown method, whose transcription can spend tokens before failing.
    """
    return MethodResult(
        method=method,
        model_name=model_name,
        label=label,
        text_preview="",
        proposal=None,
        error=reason,
        duration_ms=0,
        usage=usage,
    )
