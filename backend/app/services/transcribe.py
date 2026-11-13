"""Read a document's pages into markdown with a vision model.

SPEC 6.3a, stage one. What pypdf hands back is a flat run of characters with the layout
thrown away, which is the thing a filing model most needs: a total on an invoice and a total
in a table header read identically once the table is gone. A model that transcribes the page
keeps that structure, so the second stage reasons over headings and tables rather than a
blob.

A configuration's `vision` reading (SPEC 6.3a). A failure here fails the analysis with its
reason; nothing falls back to another reading on its own.
"""

import logging
from dataclasses import dataclass

from app.models import AiTask
from app.services import ai, extraction
from app.services.connections import ResolvedStep, call_step

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


def user_prompt(instructions: str | None) -> str:
    """The text sent alongside each batch of page images."""
    if not instructions:
        return USER_PROMPT
    return f"{USER_PROMPT}\n\n{ai.INSTRUCTIONS_HEADING}\n{instructions}"


def transcribe(
    data: bytes,
    step: ResolvedStep,
    *,
    system_prompt: str = SYSTEM_PROMPT,
    instructions: str | None = None,
    parameters: ai.TaskParameters = ai.EXTRACTION_PARAMETERS,
    meter: ai.UsageMeter | None = None,
) -> Transcription:
    """Render the document's pages and have the step's model read them.

    Raises what the call raises (Unreachable, AIUnavailable, ReplyCutOff, PromptTooLong),
    and RenderUnavailable when the file could not be turned into pages at all.
    """
    pages = extraction.render_pages(data, max_pages=extraction.MAX_RENDERED_PAGES)
    if not pages:
        raise extraction.RenderUnavailable("This PDF has no pages to read.")
    prompt = user_prompt(instructions)

    def call(step: ResolvedStep) -> Transcription:
        chunks = [
            ai.request_completion(
                endpoint_url=step.endpoint_url,
                model_name=step.model_name,
                api_key=step.api_key,
                system_prompt=system_prompt,
                prompt=prompt,
                images=batch,
                usage=step.usage(meter, AiTask.extraction) if meter else None,
                timeout=step.timeout_seconds,
                parameters=parameters,
                context_limit=step.context_limit,
            )
            for batch in _batched(pages, PAGES_PER_REQUEST)
        ]
        return Transcription(markdown=_clean("\n\n".join(chunks)), model_label=step.label)

    return call_step(step, call, AiTask.extraction)


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
