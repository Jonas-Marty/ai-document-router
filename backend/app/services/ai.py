"""Ask an OpenAI-compatible model for a filename, folder, and date.

SPEC 6.3 is strict about the outcome: a proposal is either fully valid or it is a failure
with a readable reason. There is no half-valid state, because a half-valid proposal would
put a wrong folder in front of the user pre-filled and pre-trusted.
"""

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime

import httpx

from app.services.errors import AppError
from app.services.paths import is_within, normalize_path

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 30.0
MAX_TREE_DEPTH = 4
MAX_SAMPLE_FILENAMES = 8

# SPEC 7.1: the same characters the filename field forbids. The model is told about these,
# and a response that ignores the instruction is rejected rather than sanitised.
FORBIDDEN_NAME_CHARS = set('/\\:*?"<>|')

SYSTEM_PROMPT = (
    "You file scanned documents. Given a document's text and the folders available, you "
    "choose a filename, a destination folder, and the document's date.\n"
    "Reply with JSON only, with exactly these keys: suggested_name, target_folder_path, "
    "document_date, confidence_score, reasoning_text.\n"
    '- suggested_name: no file extension, and none of these characters: / \\ : * ? " < > |\n'
    "- target_folder_path: an absolute path chosen from the folder list, nothing invented\n"
    "- document_date: the date shown on the document as YYYY-MM-DD, or null if absent\n"
    "- confidence_score: 0.0 to 1.0\n"
    "- reasoning_text: one or two plain sentences citing what you saw. Not markdown."
)


class AIUnavailable(AppError):
    code = "ai_unavailable"
    status_code = 503


class ProposalRejected(Exception):
    """The model replied, but the reply was not usable. Carries the user-facing reason."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class Proposal:
    suggested_name: str
    target_folder_path: str
    document_date: date | None
    confidence_score: float
    reasoning_text: str
    model_name: str


def build_prompt(
    text: str,
    folder_tree: list[str],
    sample_filenames: list[str],
    filename_pattern_hint: str | None,
) -> str:
    """Assemble the user message per SPEC 6.3."""
    sections = [
        "Folders available (choose exactly one):",
        "\n".join(_render_tree(folder_tree)) or "(none)",
    ]

    if sample_filenames:
        sections += [
            "",
            "Existing filenames, so you can match the naming convention:",
            "\n".join(f"  {name}" for name in sample_filenames[:MAX_SAMPLE_FILENAMES]),
        ]

    if filename_pattern_hint:
        sections += ["", f"Preferred naming pattern: {filename_pattern_hint}"]

    sections += ["", "Document text:", text or "(no text could be extracted)"]
    return "\n".join(sections)


def _render_tree(paths: list[str]) -> list[str]:
    """Render absolute paths as an indented list, capped at MAX_TREE_DEPTH."""
    rendered = []
    for path in sorted(set(paths)):
        depth = path.count("/") - 1
        if depth >= MAX_TREE_DEPTH:
            continue
        rendered.append(f"{'  ' * depth}{path}")
    return rendered


def request_proposal(
    *,
    endpoint_url: str,
    model_name: str,
    api_key: str | None,
    prompt: str,
    allowed_roots: list[str],
    client: httpx.Client | None = None,
) -> Proposal:
    """Call the model and validate its reply.

    Raises AIUnavailable if the endpoint could not be reached (after one retry), or
    ProposalRejected if it replied with something unusable.
    """
    payload: Mapping[str, object] = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    url = f"{endpoint_url.rstrip('/')}/chat/completions"

    body = _post_with_retry(url, payload, headers, client)
    content = _extract_message_content(body)
    return _validate(content, model_name, allowed_roots)


def _post_with_retry(
    url: str,
    payload: Mapping[str, object],
    headers: Mapping[str, str],
    client: httpx.Client | None,
) -> dict[str, object]:
    """POST with a single retry on 5xx or timeout, per SPEC 6.3.

    4xx is not retried: a bad key or a wrong model name fails the same way twice, and
    retrying only doubles the user's wait.
    """
    owned = client is None
    http = client or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        last_error = ""
        for attempt in (1, 2):
            try:
                response = http.post(url, json=dict(payload), headers=dict(headers))
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = f"the request timed out or the host was unreachable ({exc})"
                logger.warning("AI request attempt %d failed: %s", attempt, exc)
                continue

            if response.status_code >= 500:
                last_error = f"the model server returned {response.status_code}"
                logger.warning("AI request attempt %d got %d", attempt, response.status_code)
                continue

            if response.status_code >= 400:
                raise AIUnavailable(
                    f"The AI endpoint rejected the request ({response.status_code}). "
                    "Check the model name and API key in Settings."
                )

            try:
                parsed = response.json()
            except ValueError as exc:
                raise AIUnavailable("The AI endpoint returned a non-JSON response.") from exc
            if not isinstance(parsed, dict):
                raise AIUnavailable("The AI endpoint returned an unexpected response shape.")
            return parsed

        raise AIUnavailable(f"Couldn't reach the AI endpoint: {last_error}.")
    finally:
        if owned:
            http.close()


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


def _validate(content: str, model_name: str, allowed_roots: list[str]) -> Proposal:
    try:
        parsed = json.loads(content)
    except ValueError as exc:
        raise ProposalRejected(f"The model's reply wasn't valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ProposalRejected("The model's reply wasn't a JSON object.")

    return Proposal(
        suggested_name=_validate_name(parsed.get("suggested_name")),
        target_folder_path=_validate_folder(parsed.get("target_folder_path"), allowed_roots),
        document_date=_validate_date(parsed.get("document_date")),
        confidence_score=_validate_confidence(parsed.get("confidence_score")),
        reasoning_text=_validate_reasoning(parsed.get("reasoning_text")),
        model_name=model_name,
    )


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
    if "." in name:
        # SPEC 4.1: suggested_name is stored *without* an extension; the real one is carried
        # over from the source file. A model that appends ".pdf" would produce "x.pdf.pdf".
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


def _validate_date(value: object) -> date | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ProposalRejected("The model returned a document date that wasn't text.")
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
