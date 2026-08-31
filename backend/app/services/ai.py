"""Ask an OpenAI-compatible model for a filename, folder, and date.

SPEC 6.3 is strict about the outcome: a proposal is either fully valid or it is a failure
with a readable reason. There is no half-valid state, because a half-valid proposal would
put a wrong folder in front of the user pre-filled and pre-trusted.
"""

import base64
import json
import logging
import mimetypes
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime

import httpx

from app.services.errors import AppError
from app.services.paths import is_within, normalize_path

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 30.0
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
    """Render absolute paths as an indented list, capped at MAX_TREE_DEPTH and MAX_TREE_FOLDERS.

    When the count cap bites, shallower folders are kept: they are the plausible filing
    targets, and anything dropped can still be typed by hand in the review form. Keeping the
    *alphabetically* first N instead would silently delete every folder past the letter it
    ran out at, which reads as a much stranger tree than a truncated one.
    """
    within_depth = sorted({path for path in paths if path.count("/") - 1 < MAX_TREE_DEPTH})
    kept = sorted(sorted(within_depth, key=lambda p: (p.count("/"), p))[:MAX_TREE_FOLDERS])

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
) -> Proposal:
    """Call the model and validate its reply.

    `images` sends rendered pages alongside the prompt, for a document whose text could not
    be extracted -- the model reads the scan itself instead of being handed a transcription.
    It is the same call either way: one request that returns a proposal, not an OCR step
    bolted in front of a second one.

    Raises AIUnavailable if the endpoint could not be reached (after one retry), or
    ProposalRejected if it replied with something unusable.
    """
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    url = f"{endpoint_url.rstrip('/')}/chat/completions"

    owned = client is None
    http = client or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        try:
            body = _post_with_retry(
                url, _payload(model_name, prompt, images, json_mode=True), headers, http
            )
        except _Rejected as rejected:
            # SPEC 6.3 asks for response_format JSON, but the endpoint is whatever the user
            # configured, and plenty of OpenAI-compatible servers answer 400 to that field
            # instead of ignoring it. The system prompt already demands JSON-only, so one
            # plain retry beats failing the document over an optional field. Only 400: a 401
            # or a 404 means the key or the URL is wrong, and would fail identically twice.
            if rejected.status_code != 400:
                raise AIUnavailable(rejected.message) from rejected
            logger.warning(
                "AI endpoint refused the request (400: %s); retrying without response_format",
                rejected.detail or "no reason given",
            )
            try:
                body = _post_with_retry(
                    url, _payload(model_name, prompt, images, json_mode=False), headers, http
                )
            except _Rejected as plain:
                raise AIUnavailable(plain.message) from plain
    finally:
        if owned:
            http.close()

    content = _extract_message_content(body)
    return _validate(content, model_name, allowed_roots)


def request_completion(
    *,
    endpoint_url: str,
    model_name: str,
    api_key: str | None,
    system_prompt: str,
    prompt: str,
    images: list[bytes] | None = None,
    client: httpx.Client | None = None,
) -> str:
    """Call the model and hand back its reply verbatim.

    The prose counterpart to request_proposal: no JSON mode and no validation, because the
    answer wanted here is a transcription rather than a set of fields. Raises AIUnavailable
    for anything that stopped the endpoint from replying, so a caller walking a fallback
    chain sees endpoint trouble as endpoint trouble.
    """
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    url = f"{endpoint_url.rstrip('/')}/chat/completions"
    payload: dict[str, object] = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": _user_content(prompt, images)},
        ],
    }

    owned = client is None
    http = client or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        body = _post_with_retry(url, payload, headers, http)
    except _Rejected as rejected:
        raise AIUnavailable(rejected.message) from rejected
    finally:
        if owned:
            http.close()

    return _extract_message_content(body)


def _payload(
    model_name: str, prompt: str, images: list[bytes] | None, *, json_mode: bool
) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_content(prompt, images)},
        ],
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    return payload


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
    """POST with a single retry on 5xx or timeout, per SPEC 6.3.

    4xx is not retried here: a bad key or a wrong model name fails the same way twice, and
    retrying only doubles the user's wait. It is raised as _Rejected rather than AIUnavailable
    so the caller can decide whether the request itself is worth adjusting.
    """
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
            raise _Rejected(response.status_code, _provider_detail(response))

        try:
            parsed = response.json()
        except ValueError as exc:
            raise AIUnavailable("The AI endpoint returned a non-JSON response.") from exc
        if not isinstance(parsed, dict):
            raise AIUnavailable("The AI endpoint returned an unexpected response shape.")
        return parsed

    raise AIUnavailable(f"Couldn't reach the AI endpoint: {last_error}.")


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


def _validate(content: str, model_name: str, allowed_roots: list[str]) -> Proposal:
    try:
        parsed = json.loads(_strip_code_fence(content))
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
