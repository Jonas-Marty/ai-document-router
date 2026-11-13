"""Ask an OpenAI-compatible model for a filename, folder, and date.

SPEC 6.3 is strict about the outcome: a proposal is either fully valid or it is a failure
with a readable reason. There is no half-valid state, because a half-valid proposal would
put a wrong folder in front of the user pre-filled and pre-trusted.
"""

import base64
import json
import logging
import math
import mimetypes
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import TypeGuard

import httpx

from app.models import AiJsonMode, AiThinking
from app.services import metrics, naming
from app.services.errors import AppError
from app.services.paths import is_within, normalize_path

logger = logging.getLogger(__name__)

# The default reply timeout; each endpoint sets its own (AiEndpoint.timeout_seconds).
REQUEST_TIMEOUT_SECONDS = 120.0
CONNECT_TIMEOUT_SECONDS = 5.0
# A server error that took longer than this to arrive is not retried (see _post_with_retry).
SLOW_SERVER_ERROR_SECONDS = 5.0
# Listing models backs a button someone is waiting on, so it fails fast rather than
# holding the Settings form for the proposal timeout.
MODELS_TIMEOUT_SECONDS = 10.0
MAX_TREE_DEPTH = 4
MAX_SAMPLE_FILENAMES = 8
# A real Nextcloud holds thousands of folders under an allowed root. Sending all of them
# pushes the request past the model's context window, which comes back as an opaque 400
# rather than as a readable "too long".
MAX_TREE_FOLDERS = 250

# SPEC 7.1: the same characters the filename field forbids. The model is told about these,
# and a response that ignores the instruction is rejected rather than sanitised.
FORBIDDEN_NAME_CHARS = set('/\\:*?"<>|')

SYSTEM_PROMPT = (
    "You file scanned documents. Given a document's text and the folders available, you "
    "pick out the parts its filename is built from and choose a destination folder. You do "
    "not write the filename: it is assembled from your fields as "
    "date sender account title period number.\n"
    "Reply with JSON only, with exactly these keys, in this order: reasoning_text, "
    "document_date, sender, account, title, period, number, target_folder_path, "
    "confidence_score.\n"
    "- reasoning_text: one or two plain sentences citing what you saw. Not markdown.\n"
    "- document_date: the date the document was issued, as YYYY-MM-DD, or null if none is "
    "shown\n"
    "- sender: who sent it, by its short everyday name (ZKB, Rega, WIR Bank), not the full "
    "legal name\n"
    "- account: the IBAN, account, contract or policy number the recipient holds with the "
    "sender, copied exactly as printed, or null\n"
    "- title: the document's own title or type as printed, in the document's language and in "
    "normal case (Rechnung, Kontoeröffnungsbestätigung)\n"
    "- period: the period the document covers, as printed (01.09.2026 - 30.09.2026), or null\n"
    "- number: the invoice or document number, or null\n"
    "- target_folder_path: an absolute path chosen from the folder list, nothing invented\n"
    "- confidence_score: 0.0 to 1.0\n"
    "Leave account, period and number null unless the document clearly shows one. Never fill "
    "them with something nearby: a footer code, a page number or a reference meant for the "
    "bank is not one of them.\n"
    'No field may contain any of these characters: / \\ : * ? " < > |\n'
    "The message may include additional instructions from the person whose archive this is. "
    "Follow them, unless they conflict with the reply format above."
)

# Two calls (SPEC 6.3 strategy): the fields first, then the folder with those fields known.
# A small model does each job better alone than both at once.
FIELDS_SYSTEM_PROMPT = (
    "You read scanned documents and pick out the parts their filename is built from. You do "
    "not write the filename: it is assembled from your fields as "
    "date sender account title period number.\n"
    "Reply with JSON only, with exactly these keys, in this order: reasoning_text, "
    "document_date, sender, account, title, period, number, confidence_score.\n"
    "- reasoning_text: one or two plain sentences citing what you saw. Not markdown.\n"
    "- document_date: the date the document was issued, as YYYY-MM-DD, or null if none is "
    "shown\n"
    "- sender: who sent it, by its short everyday name (ZKB, Rega, WIR Bank), not the full "
    "legal name\n"
    "- account: the IBAN, account, contract or policy number the recipient holds with the "
    "sender, copied exactly as printed, or null\n"
    "- title: the document's own title or type as printed, in the document's language and in "
    "normal case (Rechnung, Kontoeröffnungsbestätigung)\n"
    "- period: the period the document covers, as printed (01.09.2026 - 30.09.2026), or null\n"
    "- number: the invoice or document number, or null\n"
    "- confidence_score: 0.0 to 1.0\n"
    "Leave account, period and number null unless the document clearly shows one. Never fill "
    "them with something nearby: a footer code, a page number or a reference meant for the "
    "bank is not one of them.\n"
    'No field may contain any of these characters: / \\ : * ? " < > |\n'
    "The message may include additional instructions from the person whose archive this is. "
    "Follow them, unless they conflict with the reply format above."
)

FOLDER_SYSTEM_PROMPT = (
    "You file scanned documents. Given a document, what it is, and the folders available, "
    "you choose the folder it belongs in.\n"
    "Reply with JSON only, with exactly these keys, in this order: reasoning_text, "
    "target_folder_path, confidence_score.\n"
    "- reasoning_text: one or two plain sentences on why this folder. Not markdown.\n"
    "- target_folder_path: an absolute path chosen from the folder list, nothing invented\n"
    "- confidence_score: 0.0 to 1.0\n"
    "The message may include additional instructions from the person whose archive this is. "
    "Follow them, unless they conflict with the reply format above."
)

INSTRUCTIONS_HEADING = "Additional instructions from the person whose archive this is:"


class AIUnavailable(AppError):
    code = "ai_unavailable"
    status_code = 503


class PromptTooLong(AIUnavailable):
    """The prompt would not fit the connection's context limit, so it was never sent."""

    code = "prompt_too_long"


class Unreachable(AIUnavailable):
    """No connection could be made, or it dropped before a reply: the computer is off, or
    the network is. The one failure a document waits out rather than fails on (SPEC 6.3a)."""

    code = "ai_unreachable"


class ReplyCutOff(AppError):
    """The model stopped at the output limit. What it wrote is incomplete, not wrong."""

    code = "ai_reply_cut_off"
    status_code = 502


class ProposalRejected(Exception):
    """The model replied, but the reply was not usable. Carries the user-facing reason."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class TaskParameters:
    """What every call of one step sends besides the prompt (SPEC 6.3b).

    Set per configuration step; these defaults are the built-in ones. Low temperature because
    both steps copy from the page rather than invent; an explicit output limit because a
    server's own default can be small enough to cut a two-page transcription off mid-table.
    """

    temperature: float
    max_tokens: int
    # The smallest reply worth asking for. A prompt that leaves less than this of the context
    # limit is refused rather than sent to come back cut off.
    min_reply_tokens: int
    thinking: AiThinking = AiThinking.default

    def with_settings(
        self, temperature: float, max_tokens: int, thinking: AiThinking
    ) -> "TaskParameters":
        """A step's own settings, keeping this task's minimum reply (or the step's output
        limit, when that is lower -- a smaller reply than the limit is never needed)."""
        return TaskParameters(
            temperature=temperature,
            max_tokens=max_tokens,
            min_reply_tokens=min(self.min_reply_tokens, max_tokens),
            thinking=thinking,
        )

    def apply(self, payload: dict[str, object], max_tokens: int) -> None:
        payload["temperature"] = self.temperature
        payload["max_tokens"] = max_tokens
        effort = _REASONING_EFFORT.get(self.thinking)
        if effort is not None:
            payload["reasoning_effort"] = effort


# Ollama's OpenAI-compatible API maps reasoning_effort "none" to think=false for models that
# can switch thinking off; hosted APIs take the levels as they are.
_REASONING_EFFORT = {
    AiThinking.off: "none",
    AiThinking.low: "low",
    AiThinking.medium: "medium",
    AiThinking.high: "high",
}


FILING_PARAMETERS = TaskParameters(temperature=0.1, max_tokens=2048, min_reply_tokens=512)
EXTRACTION_PARAMETERS = TaskParameters(temperature=0.1, max_tokens=4096, min_reply_tokens=1024)

# Token estimate before sending (SPEC 6.3b). German text with account numbers runs at about
# 3.5 to 4 characters per token; dividing by 3 errs high, which is the side to err on when
# the alternative is Ollama silently cutting the instructions off the front of the prompt.
CHARS_PER_TOKEN_ESTIMATE = 3
# Vision models spend anything from a few hundred to a few thousand tokens on one page; this
# sits at the high end of the common ones at the 150 DPI the pages are rendered at.
TOKENS_PER_IMAGE_ESTIMATE = 1600


def estimate_prompt_tokens(system_prompt: str, prompt: str, images: int = 0) -> int:
    """A deliberately high guess at what the server will count for this request."""
    text_tokens = math.ceil((len(system_prompt) + len(prompt)) / CHARS_PER_TOKEN_ESTIMATE)
    return text_tokens + images * TOKENS_PER_IMAGE_ESTIMATE


def reply_budget(estimate: int, parameters: TaskParameters, context_limit: int | None) -> int:
    """The max_tokens to send, or PromptTooLong when the reply would not get enough room."""
    if context_limit is None:
        return parameters.max_tokens
    room = context_limit - estimate
    if room < parameters.min_reply_tokens:
        raise PromptTooLong(
            f"The prompt is about {estimate:,} tokens, which leaves no room for a reply within "
            f"this connection's context limit of {context_limit:,}. If the server holds more, "
            "raise the limit in Settings."
        )
    return min(parameters.max_tokens, room)


def call_cost(
    usage: "TokenUsage | None", price_input: float | None, price_output: float | None
) -> float | None:
    """What a call cost from its token counts; None when either side of that is missing.

    A missing price on one side counts as free rather than unknown: a provider that bills
    only output is a real thing, and the person entered the one price there is.
    """
    if usage is None or (price_input is None and price_output is None):
        return None
    return (
        usage.prompt_tokens * (price_input or 0.0) + usage.completion_tokens * (price_output or 0.0)
    ) / 1_000_000


@dataclass(frozen=True)
class Proposal:
    # Assembled by naming.assemble_name from document_date and fields, never by the model.
    suggested_name: str
    target_folder_path: str
    document_date: date | None
    confidence_score: float
    reasoning_text: str
    model_name: str
    fields: naming.NameFields | None = None


@dataclass(frozen=True)
class TokenUsage:
    """What one reply says it cost, from the OpenAI `usage` block."""

    prompt_tokens: int
    completion_tokens: int
    # Prompt tokens the provider served from its cache; part of prompt_tokens, not on top.
    cached_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class CallRecord:
    task: str
    label: str
    usage: TokenUsage | None
    duration_ms: int
    # Which connection and model answered, kept apart so usage can be grouped by either.
    endpoint_id: str | None = None
    connection: str = ""
    model: str = ""
    # No reply at all: the endpoint was down or refused. Costs nothing that was reported,
    # but is what makes a fallback happen, so the usage page counts it.
    failed: bool = False
    # Made by a step after the first, because an earlier one was unavailable.
    fallback: bool = False
    # From the connection's prices at the time of the call; None without a price or usage.
    cost: float | None = None


@dataclass(frozen=True)
class StageUsage:
    """Every call one task made during one analysis, added up."""

    model_label: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    calls: int
    # Calls whose server sent no usage block. Counted rather than ignored, so a total built
    # partly from silence is shown as "at least", not passed off as the whole cost.
    unreported_calls: int


@dataclass(frozen=True)
class UsageSummary:
    extraction: StageUsage | None
    filing: StageUsage | None

    @property
    def total_tokens(self) -> int:
        return sum(stage.total_tokens for stage in (self.extraction, self.filing) if stage)

    def to_json(self) -> dict[str, object]:
        return {
            "extraction": asdict(self.extraction) if self.extraction else None,
            "filing": asdict(self.filing) if self.filing else None,
            "total_tokens": self.total_tokens,
        }

    def describe(self) -> str:
        """One log-friendly line: "extraction 3,412 tokens (Local · v, 2 calls) · total 3,412"."""
        parts = []
        for name, stage in (("extraction", self.extraction), ("filing", self.filing)):
            if stage is None:
                continue
            calls = f", {stage.calls} calls" if stage.calls > 1 else ""
            unreported = f", {stage.unreported_calls} unreported" if stage.unreported_calls else ""
            parts.append(
                f"{name} {stage.total_tokens:,} tokens ({stage.model_label}{calls}{unreported})"
            )
        parts.append(f"total {self.total_tokens:,}")
        return " · ".join(parts)


class UsageMeter:
    """Collects the cost of every model call made for one analysis of one document.

    An analysis is several calls -- page batches, fallback steps, then filing -- made from
    different modules. Each call site gets a recorder bound to its task and step, and the
    recording happens in this module the moment a reply arrives, before it is validated: a
    reply that turns out to be unusable was still paid for.
    """

    def __init__(self) -> None:
        self.calls: list[CallRecord] = []
        self.started = time.monotonic()

    def elapsed_ms(self) -> int:
        """Wall time since the analysis began -- reading, waiting and retrying included."""
        return int((time.monotonic() - self.started) * 1000)

    def scope(
        self,
        task: str,
        label: str,
        *,
        endpoint_id: str | None = None,
        connection: str = "",
        model: str = "",
        fallback: bool = False,
        price_input: float | None = None,
        price_output: float | None = None,
    ) -> "UsageRecorder":
        return UsageRecorder(
            self,
            task,
            label,
            endpoint_id,
            connection,
            model,
            fallback,
            price_input,
            price_output,
        )

    def summary(self) -> UsageSummary | None:
        """None when nothing was called, which is different from a call that cost nothing."""
        if all(call.failed for call in self.calls):
            return None
        return UsageSummary(extraction=self._stage("extraction"), filing=self._stage("filing"))

    def _stage(self, task: str) -> StageUsage | None:
        # A call that got no reply is a fact about the endpoint, not a cost of the analysis.
        calls = [call for call in self.calls if call.task == task and not call.failed]
        if not calls:
            return None
        reported = [call.usage for call in calls if call.usage is not None]
        prompt = sum(usage.prompt_tokens for usage in reported)
        completion = sum(usage.completion_tokens for usage in reported)
        return StageUsage(
            model_label=calls[-1].label,
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
            calls=len(calls),
            unreported_calls=len(calls) - len(reported),
        )


@dataclass(frozen=True)
class UsageRecorder:
    meter: UsageMeter
    task: str
    label: str
    endpoint_id: str | None = None
    connection: str = ""
    model: str = ""
    fallback: bool = False
    price_input: float | None = None
    price_output: float | None = None

    def _call(self, usage: TokenUsage | None, duration_ms: int, failed: bool) -> CallRecord:
        return CallRecord(
            task=self.task,
            label=self.label,
            usage=usage,
            duration_ms=duration_ms,
            endpoint_id=self.endpoint_id,
            connection=self.connection or self.label,
            model=self.model,
            failed=failed,
            fallback=self.fallback,
            cost=call_cost(usage, self.price_input, self.price_output),
        )

    def record_failure(self, duration_ms: int) -> None:
        self.meter.calls.append(self._call(None, duration_ms, failed=True))

    def record(self, usage: TokenUsage | None, duration_ms: int) -> None:
        self.meter.calls.append(self._call(usage, duration_ms, failed=False))
        # Counted here too, so /metrics and the stored usage come from the same moment and
        # cannot disagree about which calls happened.
        if usage is None:
            metrics.ai_unreported_calls.labels(self.task, self.label).inc()
        else:
            metrics.ai_tokens.labels(self.task, self.label, "prompt").inc(usage.prompt_tokens)
            metrics.ai_tokens.labels(self.task, self.label, "completion").inc(
                usage.completion_tokens
            )


def build_prompt(
    text: str,
    folder_tree: list[str],
    sample_filenames: list[str],
    filename_pattern_hint: str | None,
    instructions: str | None = None,
    *,
    max_folders: int = MAX_TREE_FOLDERS,
    max_examples: int = MAX_SAMPLE_FILENAMES,
    with_folders: bool = True,
    knowledge: str = "",
) -> str:
    """Assemble the user message per SPEC 6.3.

    The person's instructions go here rather than into the system prompt. This message is
    what gets stored as `Proposal.prompt_text`, so the review screen shows the instructions
    that were in force for *that* proposal. `with_folders=False` is the fields call of the
    two-call strategy, which leaves the folder to the second call.

    `knowledge` is the archive-knowledge block (SPEC 6.3c). Pass no sample filenames when it
    holds approved examples: those show the convention better than bare names do.
    """
    sections: list[str] = []
    if with_folders:
        sections += [
            "Folders available (choose exactly one):",
            "\n".join(_render_tree(folder_tree, max_folders)) or "(none)",
        ]

    if knowledge:
        sections += ["", knowledge]

    if sample_filenames and max_examples > 0:
        sections += [
            "",
            "Existing filenames, so you can match the naming convention:",
            "\n".join(f"  {name}" for name in sample_filenames[:max_examples]),
        ]

    if filename_pattern_hint:
        sections += ["", f"Preferred naming pattern: {filename_pattern_hint}"]

    if instructions:
        sections += ["", INSTRUCTIONS_HEADING, instructions]

    sections += ["", "Document text:", text or "(no text could be extracted)"]
    return "\n".join(sections).lstrip("\n")


def build_folder_prompt(
    text: str,
    folder_tree: list[str],
    document_date: date | None,
    fields: naming.NameFields,
    instructions: str | None = None,
    *,
    max_folders: int = MAX_TREE_FOLDERS,
    knowledge: str = "",
) -> str:
    """The second call of the two-call strategy: the folder, with the fields already known."""
    described = [f"Date: {document_date.isoformat() if document_date else 'unknown'}"] + [
        f"{naming.FIELD_LABELS[key]}: {value}"
        for key, value in fields.to_json().items()
        if value is not None
    ]
    sections = [
        "Folders available (choose exactly one):",
        "\n".join(_render_tree(folder_tree, max_folders)) or "(none)",
        "",
        "What the document is:",
        "\n".join(described),
    ]
    if knowledge:
        sections += ["", knowledge]
    if instructions:
        sections += ["", INSTRUCTIONS_HEADING, instructions]
    sections += ["", "Document text:", text or "(no text could be extracted)"]
    return "\n".join(sections)


def prompt_folders(paths: list[str], max_folders: int = MAX_TREE_FOLDERS) -> list[str]:
    """The folders the prompt lists, capped at MAX_TREE_DEPTH and MAX_TREE_FOLDERS, sorted.

    When the count cap bites, shallower folders are kept: they are the plausible filing
    targets, and anything dropped can still be typed by hand in the review form. Keeping the
    *alphabetically* first N instead would silently delete every folder past the letter it
    ran out at, which reads as a much stranger tree than a truncated one.

    Also the `enum` of the JSON schema, so the model is offered exactly what it was shown.
    """
    within_depth = sorted({path for path in paths if path.count("/") - 1 < MAX_TREE_DEPTH})
    return sorted(sorted(within_depth, key=lambda p: (p.count("/"), p))[:max_folders])


def _render_tree(paths: list[str], max_folders: int = MAX_TREE_FOLDERS) -> list[str]:
    """Render absolute paths as an indented list of prompt_folders, noting what was dropped."""
    within_depth = {path for path in paths if path.count("/") - 1 < MAX_TREE_DEPTH}
    kept = prompt_folders(paths, max_folders)

    rendered = [f"{'  ' * (path.count('/') - 1)}{path}" for path in kept]
    dropped = len(within_depth) - len(kept)
    if dropped:
        rendered.append(f"(and {dropped} more folders, not shown)")
    return rendered


def request_proposal(
    *,
    endpoint_url: str,
    model_name: str,
    api_key: str | None,
    prompt: str,
    allowed_roots: list[str],
    images: list[bytes] | None = None,
    client: httpx.Client | None = None,
    usage: UsageRecorder | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
    parameters: TaskParameters = FILING_PARAMETERS,
    context_limit: int | None = None,
    json_mode: AiJsonMode = AiJsonMode.auto,
    folder_choices: list[str] | None = None,
    system_prompt: str = SYSTEM_PROMPT,
    name_template: Sequence[str] = naming.DEFAULT_TEMPLATE,
) -> Proposal:
    """Ask for the fields and the folder in one reply, and validate it.

    `images` sends rendered pages alongside the prompt, for a document whose text could not
    be extracted -- the model reads the scan itself.

    `folder_choices` become the schema's enum for the folder: what the prompt listed.

    Raises PromptTooLong before any request when the prompt would not fit `context_limit`,
    Unreachable when no connection could be made, AIUnavailable if the endpoint failed
    otherwise, or ProposalRejected if it replied with something unusable.
    """
    parsed = _request_json(
        _JsonCall(endpoint_url, model_name, api_key, system_prompt, prompt, images),
        schema=proposal_schema(folder_choices),
        client=client,
        usage=usage,
        timeout=timeout,
        parameters=parameters,
        context_limit=context_limit,
        json_mode=json_mode,
    )
    document_date = _validate_date(parsed.get("document_date"))
    fields = _validate_fields(parsed)
    return Proposal(
        suggested_name=_validate_name(_assemble(document_date, fields, name_template)),
        target_folder_path=_validate_folder(parsed.get("target_folder_path"), allowed_roots),
        document_date=document_date,
        confidence_score=_validate_confidence(parsed.get("confidence_score")),
        reasoning_text=_validate_reasoning(parsed.get("reasoning_text")),
        model_name=model_name,
        fields=fields,
    )


@dataclass(frozen=True)
class FieldsReply:
    """The first reply of the two-call strategy."""

    document_date: date | None
    fields: naming.NameFields
    suggested_name: str
    confidence_score: float
    reasoning_text: str


def request_fields(
    *,
    endpoint_url: str,
    model_name: str,
    api_key: str | None,
    prompt: str,
    client: httpx.Client | None = None,
    usage: UsageRecorder | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
    parameters: TaskParameters = FILING_PARAMETERS,
    context_limit: int | None = None,
    json_mode: AiJsonMode = AiJsonMode.auto,
    system_prompt: str = FIELDS_SYSTEM_PROMPT,
    name_template: Sequence[str] = naming.DEFAULT_TEMPLATE,
) -> FieldsReply:
    """Ask for the name fields alone. Raises as request_proposal does."""
    parsed = _request_json(
        _JsonCall(endpoint_url, model_name, api_key, system_prompt, prompt, None),
        schema=fields_schema(),
        client=client,
        usage=usage,
        timeout=timeout,
        parameters=parameters,
        context_limit=context_limit,
        json_mode=json_mode,
    )
    document_date = _validate_date(parsed.get("document_date"))
    fields = _validate_fields(parsed)
    return FieldsReply(
        document_date=document_date,
        fields=fields,
        suggested_name=_validate_name(_assemble(document_date, fields, name_template)),
        confidence_score=_validate_confidence(parsed.get("confidence_score")),
        reasoning_text=_validate_reasoning(parsed.get("reasoning_text")),
    )


def request_folder(
    *,
    endpoint_url: str,
    model_name: str,
    api_key: str | None,
    prompt: str,
    allowed_roots: list[str],
    fields: FieldsReply,
    client: httpx.Client | None = None,
    usage: UsageRecorder | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
    parameters: TaskParameters = FILING_PARAMETERS,
    context_limit: int | None = None,
    json_mode: AiJsonMode = AiJsonMode.auto,
    folder_choices: list[str] | None = None,
    system_prompt: str = FOLDER_SYSTEM_PROMPT,
) -> Proposal:
    """Ask for the folder, given the fields of the first call, and combine both into one
    proposal: reasoning joined, confidence the lower of the two (SPEC 6.3)."""
    parsed = _request_json(
        _JsonCall(endpoint_url, model_name, api_key, system_prompt, prompt, None),
        schema=folder_schema(folder_choices),
        client=client,
        usage=usage,
        timeout=timeout,
        parameters=parameters,
        context_limit=context_limit,
        json_mode=json_mode,
    )
    reasoning = [fields.reasoning_text, _validate_reasoning(parsed.get("reasoning_text"))]
    return Proposal(
        suggested_name=fields.suggested_name,
        target_folder_path=_validate_folder(parsed.get("target_folder_path"), allowed_roots),
        document_date=fields.document_date,
        confidence_score=min(
            fields.confidence_score, _validate_confidence(parsed.get("confidence_score"))
        ),
        reasoning_text="\n".join(part for part in reasoning if part),
        model_name=model_name,
        fields=fields.fields,
    )


@dataclass(frozen=True)
class _JsonCall:
    endpoint_url: str
    model_name: str
    api_key: str | None
    system_prompt: str
    prompt: str
    images: list[bytes] | None


def _request_json(
    call: _JsonCall,
    *,
    schema: dict[str, object],
    client: httpx.Client | None,
    usage: UsageRecorder | None,
    timeout: float,
    parameters: TaskParameters,
    context_limit: int | None,
    json_mode: AiJsonMode,
) -> dict[str, object]:
    """Send one JSON-mode request and return the reply's object, or raise."""
    max_tokens = reply_budget(
        estimate_prompt_tokens(call.system_prompt, call.prompt, len(call.images or [])),
        parameters,
        context_limit,
    )
    headers = {"Authorization": f"Bearer {call.api_key}"} if call.api_key else {}
    url = f"{call.endpoint_url.rstrip('/')}/chat/completions"
    started = time.monotonic()

    owned = client is None
    http = client or _client(timeout)
    try:
        formats = _formats_to_try(json_mode, (url, call.model_name))
        for position, response_format in enumerate(formats):
            payload = _payload(call, response_format, schema)
            parameters.apply(payload, max_tokens)
            try:
                body = _post_with_retry(url, payload, headers, http)
            except _Rejected as rejected:
                # Only 400: a 401 or a 404 means the key or the URL is wrong, and would fail
                # the same way whatever format was asked for.
                if rejected.status_code != 400 or position == len(formats) - 1:
                    raise AIUnavailable(rejected.message) from rejected
                logger.warning(
                    "AI endpoint refused response_format %s (400: %s); trying %s",
                    response_format,
                    rejected.detail or "no reason given",
                    formats[position + 1] or "none",
                )
                continue
            if json_mode == AiJsonMode.auto:
                _known_format[(url, call.model_name)] = response_format
            break
    except AIUnavailable:
        _record_failure(usage, started)
        raise
    finally:
        if owned:
            http.close()

    _record_usage(usage, body, started)
    if _finish_reason(body) == "length":
        raise ProposalRejected(
            f"The model's reply was cut off at the output limit of {max_tokens:,} tokens."
        )
    return _parse_object(_extract_message_content(body))


def _formats_to_try(json_mode: AiJsonMode, cache_key: tuple[str, str]) -> list[str | None]:
    """The response_format values to ask with, in order (SPEC 6.3).

    In auto mode servers disagree on how to be asked: some want json_schema, some only
    json_object, some 400 on either. json_schema goes first because constraining a small model
    to the schema -- the folder an enum of the real ones -- is worth more than anything the
    prompt can say. What worked is remembered so the next document goes straight there.
    """
    if json_mode == AiJsonMode.none:
        return [None]
    if json_mode != AiJsonMode.auto:
        return [str(json_mode)]
    start = _known_format.get(cache_key, RESPONSE_FORMATS[0])
    return RESPONSE_FORMATS[RESPONSE_FORMATS.index(start) :]


def _finish_reason(body: dict[str, object]) -> object:
    choices = body.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        return choices[0].get("finish_reason")
    return None


def request_completion(
    *,
    endpoint_url: str,
    model_name: str,
    api_key: str | None,
    system_prompt: str,
    prompt: str,
    images: list[bytes] | None = None,
    client: httpx.Client | None = None,
    usage: UsageRecorder | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
    parameters: TaskParameters = EXTRACTION_PARAMETERS,
    context_limit: int | None = None,
) -> str:
    """Call the model and hand back its reply verbatim.

    The prose counterpart to request_proposal: no JSON mode and no validation, because the
    answer wanted here is a transcription rather than a set of fields. Raises AIUnavailable
    for anything that stopped the endpoint from replying, so a caller walking a fallback
    chain sees endpoint trouble as endpoint trouble; PromptTooLong, one of those, before any
    request; ReplyCutOff when the reply stopped at the output limit, since half a page read
    is not a reading.
    """
    max_tokens = reply_budget(
        estimate_prompt_tokens(system_prompt, prompt, len(images or [])),
        parameters,
        context_limit,
    )
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    url = f"{endpoint_url.rstrip('/')}/chat/completions"
    payload: dict[str, object] = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": _user_content(prompt, images)},
        ],
    }
    parameters.apply(payload, max_tokens)

    started = time.monotonic()
    owned = client is None
    http = client or _client(timeout)
    try:
        body = _post_with_retry(url, payload, headers, http)
    except _Rejected as rejected:
        _record_failure(usage, started)
        raise AIUnavailable(rejected.message) from rejected
    except AIUnavailable:
        _record_failure(usage, started)
        raise
    finally:
        if owned:
            http.close()

    _record_usage(usage, body, started)
    if _finish_reason(body) == "length":
        raise ReplyCutOff(
            f"The model's reply was cut off at the output limit of {max_tokens:,} tokens."
        )
    return _extract_message_content(body)


def _record_usage(recorder: UsageRecorder | None, body: dict[str, object], started: float) -> None:
    if recorder is not None:
        recorder.record(_extract_usage(body), int((time.monotonic() - started) * 1000))


def _record_failure(recorder: UsageRecorder | None, started: float) -> None:
    if recorder is not None:
        recorder.record_failure(int((time.monotonic() - started) * 1000))


def _extract_usage(body: dict[str, object]) -> TokenUsage | None:
    """The reply's token counts, or None when the server did not send usable ones.

    Plenty of local servers omit `usage`, and some send only `total_tokens`. Neither is an
    error: the call still happened, and the meter counts it as unreported.
    """
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    if _is_count(prompt) and _is_count(completion):
        return TokenUsage(
            prompt_tokens=prompt, completion_tokens=completion, cached_tokens=_cached(usage)
        )
    return None


def _cached(usage: dict[object, object]) -> int:
    """Cached prompt tokens wherever the server reports them: OpenAI's
    `prompt_tokens_details.cached_tokens`, or Anthropic-style `cache_read_input_tokens`."""
    details = usage.get("prompt_tokens_details")
    candidates = [
        details.get("cached_tokens") if isinstance(details, dict) else None,
        usage.get("cache_read_input_tokens"),
        usage.get("cached_tokens"),
    ]
    return next((value for value in candidates if _is_count(value)), 0)


def _is_count(value: object) -> TypeGuard[int]:
    # bool is an int subclass, and `true` for a token count is a broken server, not 1 token.
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


RESPONSE_FORMATS: list[str | None] = ["json_schema", "json_object", None]
# (url, model) -> the response_format that endpoint last accepted. In memory: a restart
# costs at most one refused request per endpoint to find out again.
_known_format: dict[tuple[str, str], str | None] = {}


REPLY_KEYS = (
    "reasoning_text",
    "document_date",
    "sender",
    "account",
    "title",
    "period",
    "number",
    "target_folder_path",
    "confidence_score",
)


_FIELD_PROPERTIES: dict[str, dict[str, object]] = {
    "document_date": {"type": ["string", "null"]},
    "sender": {"type": "string"},
    "account": {"type": ["string", "null"]},
    "title": {"type": "string"},
    "period": {"type": ["string", "null"]},
    "number": {"type": ["string", "null"]},
}


def _schema(properties: dict[str, dict[str, object]]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _folder_property(folder_choices: list[str] | None) -> dict[str, object]:
    folder: dict[str, object] = {"type": "string"}
    if folder_choices:
        folder["enum"] = list(folder_choices)
    return folder


def proposal_schema(folder_choices: list[str] | None = None) -> dict[str, object]:
    """The reply's JSON schema; with folder choices, the folder must be one of them.

    Order matters: a model writes its reply front to back, so the reasoning that settles the
    fields comes before them, in the order the name is assembled in.
    """
    return _schema(
        {
            "reasoning_text": {"type": "string"},
            **_FIELD_PROPERTIES,
            "target_folder_path": _folder_property(folder_choices),
            "confidence_score": {"type": "number"},
        }
    )


def fields_schema() -> dict[str, object]:
    """The first reply of the two-call strategy: the fields without the folder."""
    return _schema(
        {
            "reasoning_text": {"type": "string"},
            **_FIELD_PROPERTIES,
            "confidence_score": {"type": "number"},
        }
    )


def folder_schema(folder_choices: list[str] | None = None) -> dict[str, object]:
    """The second reply of the two-call strategy."""
    return _schema(
        {
            "reasoning_text": {"type": "string"},
            "target_folder_path": _folder_property(folder_choices),
            "confidence_score": {"type": "number"},
        }
    )


def _payload(
    call: "_JsonCall", response_format: str | None, schema: dict[str, object]
) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": call.model_name,
        "messages": [
            {"role": "system", "content": call.system_prompt},
            {"role": "user", "content": _user_content(call.prompt, call.images)},
        ],
    }
    if response_format == "json_object":
        payload["response_format"] = {"type": "json_object"}
    elif response_format == "json_schema":
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "proposal", "schema": schema},
        }
    return payload


def _client(timeout: float) -> httpx.Client:
    """Connecting gets a few seconds whatever the reply timeout: a computer that is switched
    off should be skipped almost at once, not after the minutes a slow model may need."""
    return httpx.Client(timeout=httpx.Timeout(timeout, connect=CONNECT_TIMEOUT_SECONDS))


def _user_content(prompt: str, images: list[bytes] | None) -> object:
    """A plain string with no images, the OpenAI content-parts array with them.

    Kept as a bare string in the text case rather than a one-element array: every
    OpenAI-compatible server accepts the string form, while the parts form is exactly the
    kind of thing a smaller local server implements only for the models that need it.
    """
    if not images:
        return prompt
    parts: list[dict[str, object]] = [{"type": "text", "text": prompt}]
    for image in images:
        encoded = base64.b64encode(image).decode()
        parts.append(
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}}
        )
    return parts


def list_models(
    *,
    endpoint_url: str,
    api_key: str | None,
    client: httpx.Client | None = None,
) -> list[str]:
    """GET the OpenAI-compatible `/models` list, sorted, for the Settings model picker.

    No retry, unlike request_proposal: this backs a button, and a person watching a spinner
    would rather see the failure and press it again than wait through a second timeout.
    """
    url = f"{endpoint_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    owned = client is None
    http = client or httpx.Client(timeout=MODELS_TIMEOUT_SECONDS)
    try:
        try:
            response = http.get(url, headers=headers)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            logger.warning("Listing models from %s failed: %s", url, exc)
            raise AIUnavailable(f"Couldn't reach the AI endpoint: {exc}.") from exc

        if response.status_code in (401, 403):
            # 403 is not always "bad key" -- it is also how a provider says the plan does not
            # cover this endpoint -- so pass on the reason when there is one.
            detail = _json_error_detail(response)
            raise AIUnavailable(
                f"The AI endpoint rejected the API key ({response.status_code})."
                + (f" It said: {detail}" if detail else "")
            )
        if response.status_code >= 400:
            # Only a structured reason, not the raw body: a wrong URL answers with an HTML
            # page, and quoting 300 characters of markup at the user would bury the one
            # sentence that actually tells them what to change.
            detail = _json_error_detail(response)
            raise AIUnavailable(
                f"The AI endpoint returned {response.status_code} for {url}. "
                + (
                    f"It said: {detail}"
                    if detail
                    else "Check that the URL is the API base, not a documentation page."
                )
            )

        try:
            parsed = response.json()
        except ValueError as exc:
            raise AIUnavailable(
                "The AI endpoint returned a non-JSON response. Check that the URL is the "
                "API base, not a documentation page."
            ) from exc
    finally:
        if owned:
            http.close()

    return _extract_model_ids(parsed)


def _extract_model_ids(parsed: object) -> list[str]:
    if not isinstance(parsed, dict):
        raise AIUnavailable("The AI endpoint returned an unexpected response shape.")
    data = parsed.get("data")
    if not isinstance(data, list):
        raise AIUnavailable("The AI endpoint's model list had no 'data' array.")

    ids = {
        entry["id"]
        for entry in data
        if isinstance(entry, dict) and isinstance(entry.get("id"), str) and entry["id"].strip()
    }
    if not ids and data:
        raise AIUnavailable("The AI endpoint listed models without usable ids.")
    return sorted(ids)


class _Rejected(Exception):
    """A 4xx from the endpoint, carrying whatever reason it gave for it."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)

    @property
    def message(self) -> str:
        base = f"The AI endpoint rejected the request ({self.status_code})."
        if not self.detail:
            return f"{base} Check the model name and API key in Settings."
        return f"{base} It said: {self.detail}"


def _post_with_retry(
    url: str,
    payload: Mapping[str, object],
    headers: Mapping[str, str],
    http: httpx.Client,
) -> dict[str, object]:
    """POST with a single retry on 5xx or a failed connection, per SPEC 6.3.

    A reply that times out is not retried: the timeout is already the longest the endpoint
    is allowed, and a second wait would only double it before the next step is tried.

    4xx is not retried here: a bad key or a wrong model name fails the same way twice, and
    retrying only doubles the user's wait. It is raised as _Rejected rather than AIUnavailable
    so the caller can decide whether the request itself is worth adjusting.
    """
    last_error = ""
    # Whether the last attempt failed for want of a connection rather than with an answer.
    unreachable = False
    for attempt in (1, 2):
        started = time.monotonic()
        try:
            response = http.post(url, json=dict(payload), headers=dict(headers))
        except httpx.ReadTimeout as exc:
            # The server took the request and did not answer within the endpoint's timeout.
            # Asking again would only double a wait that is already the longest allowed.
            raise AIUnavailable(
                "The AI endpoint didn't answer within its reply timeout. If it is just slow "
                "(a large local model, say), raise the timeout in Settings."
            ) from exc
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = f"the request timed out or the host was unreachable ({exc})"
            logger.warning("AI request attempt %d failed: %s", attempt, exc)
            unreachable = True
            continue

        unreachable = False
        if response.status_code >= 500:
            detail = _provider_detail(response)
            last_error = f"the model server returned {response.status_code}" + (
                f": {detail}" if detail else ""
            )
            logger.warning("AI request attempt %d got %d", attempt, response.status_code)
            # A slow 5xx is a proxy (LiteLLM, say) that already tried its upstream, with its
            # own retries, and gave up: asking again repeats that whole wait for the same
            # answer. Only a quick one is worth a second try.
            if time.monotonic() - started > SLOW_SERVER_ERROR_SECONDS:
                break
            continue

        if response.status_code >= 400:
            raise _Rejected(response.status_code, _provider_detail(response))

        try:
            parsed = response.json()
        except ValueError as exc:
            raise AIUnavailable("The AI endpoint returned a non-JSON response.") from exc
        if not isinstance(parsed, dict):
            raise AIUnavailable("The AI endpoint returned an unexpected response shape.")
        return parsed

    if unreachable:
        raise Unreachable(f"Couldn't reach the AI endpoint: {last_error}.")
    raise AIUnavailable(f"Couldn't reach the AI endpoint: {last_error}.")


PROBE_TIMEOUT_SECONDS = 10.0


def probe(*, endpoint_url: str, api_key: str | None, client: httpx.Client | None = None) -> bool:
    """Whether the server answers `/models` with any HTTP status (SPEC 6.3a).

    Cheap and quick on purpose: it runs before a batch's first call to a connection, so a
    computer that is switched off costs five seconds once per tick, not a reply timeout per
    document.
    """
    url = f"{endpoint_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    owned = client is None
    http = client or httpx.Client(
        timeout=httpx.Timeout(PROBE_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS)
    )
    try:
        http.get(url, headers=headers)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        logger.info("AI endpoint %s is unreachable: %s", endpoint_url, exc)
        return False
    finally:
        if owned:
            http.close()
    return True


def _provider_detail(response: httpx.Response) -> str:
    """The reason the endpoint gave for refusing, if it gave one.

    Without this every 400 reads the same, and an unsupported field, a context overflow and
    an unknown model are indistinguishable to the person who has to fix the configuration.
    Only the response body is read; the API key travels in a header, so it cannot appear here
    even from an endpoint that echoes the request back.
    """
    return _json_error_detail(response) or _shorten(response.text)


def _json_error_detail(response: httpx.Response) -> str:
    """The error message out of a JSON body, in the shapes providers actually use."""
    try:
        parsed = response.json()
    except ValueError:
        return ""
    if not isinstance(parsed, dict):
        return ""

    error = parsed.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return _shorten(error["message"])
    for key in ("error", "detail", "message"):
        value = parsed.get(key)
        if isinstance(value, str):
            return _shorten(value)
    return ""


def _shorten(text: str, limit: int = 300) -> str:
    """Collapse to one line and cap it: this ends up in a form field, not a log viewer."""
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else f"{collapsed[:limit]}\u2026"


def _extract_message_content(body: dict[str, object]) -> str:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProposalRejected("The model returned no choices.")
    first = choices[0]
    if not isinstance(first, dict):
        raise ProposalRejected("The model returned a malformed choice.")
    message = first.get("message")
    if not isinstance(message, dict):
        raise ProposalRejected("The model returned a malformed message.")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ProposalRejected("The model returned an empty message.")
    return content


def _strip_code_fence(content: str) -> str:
    """Unwrap a ```json ... ``` block if there is one.

    Needed on the no-response_format path: without JSON mode the model is only following the
    prompt's "JSON only" by its own lights, and a markdown fence is the usual way that goes.
    """
    text = content.strip()
    if not text.startswith("```"):
        return text
    after_opening = text[3:].partition("\n")[2]
    if not after_opening:
        return text
    body, fence, _ = after_opening.rpartition("```")
    return (body if fence else after_opening).strip()


def _parse_object(content: str) -> dict[str, object]:
    try:
        parsed = json.loads(_strip_code_fence(content))
    except ValueError as exc:
        raise ProposalRejected(f"The model's reply wasn't valid JSON: {exc}") from exc
    # Some models wrap their one answer in a list. One object in a list is still one answer.
    if isinstance(parsed, list) and len(parsed) == 1:
        parsed = parsed[0]
    if not isinstance(parsed, dict):
        raise ProposalRejected("The model's reply wasn't a JSON object.")
    return parsed


def _validate_fields(parsed: dict[str, object]) -> naming.NameFields:
    """The name fields, cleaned (SPEC 7.1a). A word for "none" in any field is none.

    Dots, spaces and dashes are trimmed off each field's ends: a title like "Rechnung Nr."
    is a good answer, and the name it ends would only be refused for its trailing dot.
    """
    values: dict[str, object] = {}
    for key in naming.FIELD_LABELS:
        value = parsed.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool):
            # An invoice number the model wrote as a JSON number is still the number.
            value = str(value)
        if isinstance(value, str):
            value = value.strip().strip(naming.EDGE_CHARS)
            if value.lower() in _NULLISH:
                # In a required field this then reads as missing, which is what it is.
                value = None
        values[key] = value
    try:
        return naming.clean_fields(values)
    except AppError as exc:
        raise ProposalRejected(f"The model's fields were unusable: {exc.message}") from exc


def _assemble(
    document_date: date | None, fields: naming.NameFields, template: Sequence[str]
) -> str:
    try:
        return naming.assemble_name(document_date, fields, template)
    except AppError as exc:
        raise ProposalRejected(f"The name built from the model's fields: {exc.message}") from exc


def _validate_name(value: object) -> str:
    if not isinstance(value, str):
        raise ProposalRejected("The model didn't return a filename.")
    name = value.strip()
    if not name:
        raise ProposalRejected("The model returned an empty filename.")
    if len(name) > 200:
        raise ProposalRejected("The model returned a filename longer than 200 characters.")
    if FORBIDDEN_NAME_CHARS & set(name):
        raise ProposalRejected("The model's filename contained forbidden characters.")
    if any(ord(char) < 32 for char in name):
        raise ProposalRejected("The model's filename contained control characters.")
    if ".." in name:
        raise ProposalRejected("The model's filename contained '..'.")
    if name != name.strip(". -"):
        raise ProposalRejected("The model's filename started or ended with a dot, space, or dash.")
    # SPEC 4.1: suggested_name is stored *without* an extension; the real one is carried
    # over from the source file, so a model that appends ".pdf" would produce "x.pdf.pdf".
    # Detected by asking whether the trailing segment is a *recognised* extension rather
    # than by looking for a dot: a real filing convention here is
    # "YYYY.MM.DD Sender Description", where the dots are date separators.
    guessed, _ = mimetypes.guess_type(name)
    if guessed is not None:
        raise ProposalRejected("The model included a file extension in the name.")
    return name


def _validate_folder(value: object, allowed_roots: list[str]) -> str:
    if not isinstance(value, str):
        raise ProposalRejected("The model didn't return a destination folder.")
    try:
        folder = normalize_path(value)
    except ValueError as exc:
        raise ProposalRejected(f"The model returned an unusable folder path: {exc}") from exc

    if not allowed_roots:
        raise ProposalRejected("No allowed folders are configured yet — set them in Settings.")
    if not any(is_within(normalize_path(root), folder) for root in allowed_roots):
        raise ProposalRejected(f"The model chose '{folder}', which is outside your folders.")
    return folder


# Models frequently express "no date" as a word rather than JSON null.
_NULLISH = {"", "null", "none", "n/a", "na", "-", "unknown", "undefined"}


def _validate_date(value: object) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProposalRejected("The model returned a document date that wasn't text.")
    if value.strip().lower() in _NULLISH:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ProposalRejected(f"The model returned an unparseable date: {value!r}") from exc


def _validate_confidence(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ProposalRejected("The model didn't return a numeric confidence score.")
    # SPEC 6.3 says clamp rather than reject: a model that says 1.2 is confident, not broken.
    return max(0.0, min(1.0, float(value)))


def _validate_reasoning(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()
