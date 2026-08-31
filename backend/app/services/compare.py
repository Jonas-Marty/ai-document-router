"""Run several ways of reading a document side by side, so a person can judge them.

The app has one production path -- extract text with pypdf, ask the configured model for a
proposal -- and no way to tell whether a different reading of the same scan would have done
better. This runs the alternatives against one document and hands back what each produced,
without changing anything: nothing here writes a proposal, and the review form is still
where a filename is chosen.

Deliberately synchronous and on demand. Every method costs an LLM call, so this is a thing
someone asks for while looking at a document, never something the poller does on a tick.
"""

import logging
import time
from dataclasses import dataclass

from app.models import AppSettings, Document
from app.services import ai, extraction, folders, ocr
from app.services import settings as settings_service
from app.services.errors import AppError
from app.services.webdav import WebDavService

logger = logging.getLogger(__name__)

TEXT_LAYER = "text_layer"
OCR = "ocr"
VISION = "vision"

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
    webdav: WebDavService, app_settings: AppSettings, document: Document
) -> list[MethodResult]:
    """Read one document every configured way and return each result in turn.

    A method that fails is a result too, not an omission: "the vision model refused this
    image" is exactly the finding someone comparing methods needs to see, and dropping it
    would leave them wondering whether it had been tried.
    """
    data = b"".join(webdav.read_stream(document.webdav_path))
    filename = document.original_filename
    mime_type = document.mime_type
    api_key = settings_service.decrypt_api_key(app_settings)
    folder_tree, sample_filenames = folders.prompt_context(webdav, app_settings)

    results: list[MethodResult] = []

    def propose(
        method: str, model_name: str, label: str, text: str, images: list[bytes] | None
    ) -> None:
        started = time.monotonic()
        prompt = ai.build_prompt(
            text, folder_tree, sample_filenames, app_settings.filename_pattern_hint
        )
        proposal: ai.Proposal | None = None
        error: str | None = None
        try:
            proposal = ai.request_proposal(
                endpoint_url=app_settings.ai_endpoint_url,
                model_name=model_name,
                api_key=api_key,
                prompt=prompt,
                allowed_roots=list(app_settings.allowed_root_folders),
                images=images,
            )
        except ai.ProposalRejected as exc:
            error = exc.reason
        except AppError as exc:
            error = exc.message

        results.append(
            MethodResult(
                method=method,
                model_name=model_name,
                label=label,
                text_preview=text[:MAX_TEXT_CHARS],
                proposal=proposal,
                error=error,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        )

    extracted = extraction.extract(data, filename, mime_type)

    # 1. What the app does today.
    if extracted.has_usable_text:
        propose(TEXT_LAYER, app_settings.ai_model_name, "Text layer", extracted.text, None)
    else:
        results.append(
            _skipped(
                TEXT_LAYER,
                app_settings.ai_model_name,
                "Text layer",
                extracted.text_error or extraction.NO_TEXT_LAYER_MESSAGE,
            )
        )

    pages, render_error = _render(data, extracted.mime_type)

    # 2. Classical OCR over the rendered pages.
    if render_error is not None:
        results.append(_skipped(OCR, app_settings.ai_model_name, "Tesseract OCR", render_error))
    else:
        try:
            text = ocr.read_pages(pages)
        except ocr.OcrUnavailable as exc:
            results.append(_skipped(OCR, app_settings.ai_model_name, "Tesseract OCR", str(exc)))
        else:
            if text:
                propose(OCR, app_settings.ai_model_name, "Tesseract OCR", text, None)
            else:
                results.append(
                    _skipped(
                        OCR,
                        app_settings.ai_model_name,
                        "Tesseract OCR",
                        "Tesseract read no text off these pages.",
                    )
                )

    # 3. Each configured vision model, reading the pages itself.
    for model_name in app_settings.vision_model_names:
        label = f"Vision · {model_name}"
        if render_error is not None:
            results.append(_skipped(VISION, model_name, label, render_error))
            continue
        # No transcription in the prompt: the point of this method is that the model reads
        # the page. Handing it text too would make it impossible to tell which it used.
        propose(VISION, model_name, label, "", pages)

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
