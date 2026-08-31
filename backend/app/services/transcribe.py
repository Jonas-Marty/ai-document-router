"""Read a document's pages into markdown with a vision model.

SPEC 6.3a, stage one. What pypdf hands back is a flat run of characters with the layout
thrown away, which is the thing a filing model most needs: a total on an invoice and a total
in a table header read identically once the table is gone. A model that transcribes the page
keeps that structure, so the second stage reasons over headings and tables rather than a
blob.

Never fatal. When there is no extraction chain configured, or every endpoint in it fails, the
caller falls back to the PDF's own text layer -- a worse input, not a stopped pipeline.
"""

import logging
from dataclasses import dataclass

from app.models import AiTask
from app.services import ai, extraction
from app.services.ai_tasks import ResolvedStep, run_chain

logger = logging.getLogger(__name__)

# Long documents are read a page at a time and stitched, so the cap is on pages sent per
# request, not on the document. Two per call keeps a single request small enough that a
# 7B model on a home GPU answers it.
PAGES_PER_REQUEST = 2

SYSTEM_PROMPT = (
    "You transcribe scanned documents into Markdown.\n"
    "Reproduce what is on the page and nothing else: no commentary, no summary, no "
    "explanation of what the document is.\n"
    "- Use headings for headings, lists for lists, and Markdown tables for tables.\n"
    "- Keep letterheads, addresses, dates, reference numbers, and totals verbatim.\n"
    "- Preserve the reading order of the page.\n"
    "- If a passage is illegible, write [illegible] rather than guessing.\n"
    "- Output only the Markdown. Do not wrap it in a code fence."
)

USER_PROMPT = "Transcribe this document into Markdown."


@dataclass(frozen=True)
class Transcription:
    markdown: str
    model_label: str


def transcribe(data: bytes, steps: list[ResolvedStep]) -> Transcription:
    """Render the document's pages and have the first working endpoint read them.

    Raises AIUnavailable when the chain is empty or every endpoint in it failed, and
    RenderUnavailable when the file could not be turned into pages at all.
    """
    pages = extraction.render_pages(data, max_pages=extraction.MAX_RENDERED_PAGES)
    if not pages:
        raise extraction.RenderUnavailable("This PDF has no pages to read.")

    def call(step: ResolvedStep) -> Transcription:
        chunks = [
            ai.request_completion(
                endpoint_url=step.endpoint_url,
                model_name=step.model_name,
                api_key=step.api_key,
                system_prompt=SYSTEM_PROMPT,
                prompt=USER_PROMPT,
                images=batch,
            )
            for batch in _batched(pages, PAGES_PER_REQUEST)
        ]
        return Transcription(markdown=_clean("\n\n".join(chunks)), model_label=step.label)

    return run_chain(steps, call, AiTask.extraction)


def _batched(pages: list[bytes], size: int) -> list[list[bytes]]:
    return [pages[start : start + size] for start in range(0, len(pages), size)]


def _clean(markdown: str) -> str:
    """Unwrap a ```markdown fence if the model added one despite being asked not to."""
    text = markdown.strip()
    if not text.startswith("```"):
        return text
    after_opening = text[3:].partition("\n")[2]
    if not after_opening:
        return text
    body, fence, _ = after_opening.rpartition("```")
    return (body if fence else after_opening).strip()
