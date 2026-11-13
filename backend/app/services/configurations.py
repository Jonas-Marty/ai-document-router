"""Configurations: named ways of reading and filing a document (SPEC 6.3a).

A configuration replaces the per-task fallback chains. It names exactly one reading and one
filing step, so what produced a proposal is always one answer, and trying something else is
a choice the person makes rather than something the app does when a computer is off.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, replace

from sqlmodel import Session, select

from app.models import (
    AiConfiguration,
    AiEndpoint,
    AiThinking,
    AppSettings,
    AuditAction,
    Document,
    FilingStrategy,
    ReadingMethod,
)
from app.services import (
    ai,
    audit,
    connections,
    extraction,
    folders,
    knowledge,
    naming,
    transcribe,
)
from app.services.audit import Actor
from app.services.connections import ResolvedStep
from app.services.errors import NotFoundError, ValidationError
from app.services.times import utc_now
from app.services.webdav import WebDavService

MAX_NAME_CHARS = 80
MAX_PROMPT_CHARS = 20_000
MAX_INSTRUCTIONS_CHARS = 2000
TEMPERATURE_RANGE = (0.0, 2.0)
MAX_TOKENS_RANGE = (256, 32_768)
MAX_EXAMPLES_RANGE = (0, 20)
MAX_FOLDERS_RANGE = (10, 1000)
MAX_TEXT_CHARS_RANGE = (1000, 30_000)


@dataclass(frozen=True)
class BuiltinPrompts:
    """What a null system prompt stands for."""

    reading: str = transcribe.SYSTEM_PROMPT
    filing_one_call: str = ai.SYSTEM_PROMPT
    filing_fields: str = ai.FIELDS_SYSTEM_PROMPT
    filing_folder: str = ai.FOLDER_SYSTEM_PROMPT


BUILTIN = BuiltinPrompts()


@dataclass(frozen=True)
class StepInput:
    """One step as the editor sends it. Endpoint and model are None for a reading that needs
    no model; a None prompt means the built-in one."""

    endpoint_id: str | None = None
    model_name: str | None = None
    system_prompt: str | None = None
    instructions: str | None = None
    temperature: float = 0.1
    max_tokens: int = 2048
    thinking: AiThinking = AiThinking.default


@dataclass(frozen=True)
class ConfigurationInput:
    name: str
    filing: StepInput
    reading_method: ReadingMethod = ReadingMethod.text_layer
    reading: StepInput = StepInput(max_tokens=ai.EXTRACTION_PARAMETERS.max_tokens)
    filing_strategy: FilingStrategy = FilingStrategy.one_call
    folder_system_prompt: str | None = None
    max_examples: int = ai.MAX_SAMPLE_FILENAMES
    max_folders: int = ai.MAX_TREE_FOLDERS
    max_text_chars: int = extraction.MAX_TEXT_CHARS


def list_configurations(session: Session, user_id: str) -> list[AiConfiguration]:
    """The default first, then by name."""
    rows = session.exec(select(AiConfiguration).where(AiConfiguration.user_id == user_id)).all()
    return sorted(rows, key=lambda row: (not row.is_default, row.name.lower()))


def get_configuration(session: Session, user_id: str, configuration_id: str) -> AiConfiguration:
    """Someone else's configuration is as missing as a deleted one (SPEC 6.5a)."""
    configuration = session.get(AiConfiguration, configuration_id)
    if configuration is None or configuration.user_id != user_id:
        raise NotFoundError("That configuration no longer exists.")
    return configuration


def default_configuration(session: Session, user_id: str) -> AiConfiguration | None:
    return session.exec(
        select(AiConfiguration).where(
            AiConfiguration.user_id == user_id, AiConfiguration.is_default
        )
    ).first()


def for_document(session: Session, document: Document) -> AiConfiguration | None:
    """The configuration picked for this document, else the owner's default."""
    if document.configuration_id is not None:
        picked = session.get(AiConfiguration, document.configuration_id)
        if picked is not None and picked.user_id == document.user_id:
            return picked
    return default_configuration(session, document.user_id)


def create(
    session: Session, user_id: str, data: ConfigurationInput, *, actor: Actor
) -> AiConfiguration:
    """The user's first configuration becomes their default (SPEC 6.3a)."""
    data = _validated(session, user_id, data, configuration_id=None)
    configuration = AiConfiguration(
        user_id=user_id,
        name=data.name,
        is_default=default_configuration(session, user_id) is None,
        created_at=utc_now(),
    )
    _apply(configuration, data)
    session.add(configuration)
    audit.record(
        session,
        AuditAction.configuration_created,
        actor=actor,
        summary=f"Added the configuration '{configuration.name}'",
        details={"name": configuration.name, **_describe(configuration)},
    )
    session.commit()
    session.refresh(configuration)
    return configuration


def update(
    session: Session,
    user_id: str,
    configuration_id: str,
    data: ConfigurationInput,
    *,
    actor: Actor,
) -> AiConfiguration:
    configuration = get_configuration(session, user_id, configuration_id)
    data = _validated(session, user_id, data, configuration_id=configuration_id)
    before = {"name": configuration.name, **_describe(configuration)}
    configuration.name = data.name
    _apply(configuration, data)
    after = {"name": configuration.name, **_describe(configuration)}
    session.add(configuration)
    audit.record(
        session,
        AuditAction.configuration_updated,
        actor=actor,
        summary=f"Edited the configuration '{configuration.name}'",
        details={
            "changed": {
                key: {"from": before[key], "to": after[key]}
                for key in before
                if before[key] != after[key]
            }
        },
    )
    session.commit()
    session.refresh(configuration)
    return configuration


def duplicate(
    session: Session, user_id: str, configuration_id: str, *, actor: Actor
) -> AiConfiguration:
    """A copy to change one thing in. Never the default: that stays a deliberate choice."""
    source = get_configuration(session, user_id, configuration_id)
    taken = {row.name for row in list_configurations(session, user_id)}
    name = _copy_name(source.name, taken)
    copy = AiConfiguration(user_id=user_id, name=name, is_default=False, created_at=utc_now())
    _apply(copy, replace(to_input(source), name=name))
    session.add(copy)
    audit.record(
        session,
        AuditAction.configuration_created,
        actor=actor,
        summary=f"Duplicated the configuration '{source.name}' as '{name}'",
        details={"name": name, "copied_from": source.name, **_describe(copy)},
    )
    session.commit()
    session.refresh(copy)
    return copy


def _copy_name(name: str, taken: set[str]) -> str:
    base = f"{name} (copy)"[:MAX_NAME_CHARS]
    candidate, counter = base, 2
    while candidate in taken:
        suffix = f" (copy {counter})"
        candidate = f"{name[: MAX_NAME_CHARS - len(suffix)]}{suffix}"
        counter += 1
    return candidate


def make_default(
    session: Session, user_id: str, configuration_id: str, *, actor: Actor
) -> AiConfiguration:
    """Documents still waiting for an analysis follow the new default; proposals stay."""
    configuration = get_configuration(session, user_id, configuration_id)
    previous = default_configuration(session, user_id)
    if previous is not None and previous.id == configuration.id:
        return configuration
    if previous is not None:
        previous.is_default = False
        session.add(previous)
    configuration.is_default = True
    session.add(configuration)
    _clear_picks(session, user_id, configuration.id)
    audit.record(
        session,
        AuditAction.configuration_defaulted,
        actor=actor,
        summary=f"Made '{configuration.name}' the default configuration",
        details={"from": previous.name if previous else None, "to": configuration.name},
    )
    session.commit()
    session.refresh(configuration)
    return configuration


def delete(session: Session, user_id: str, configuration_id: str, *, actor: Actor) -> None:
    """Refused for the default. Documents that picked this one go back to the default."""
    configuration = get_configuration(session, user_id, configuration_id)
    if configuration.is_default:
        raise ValidationError(
            f"'{configuration.name}' is the default configuration. Make another one the "
            "default first."
        )
    _clear_picks(session, user_id, configuration.id)
    session.delete(configuration)
    audit.record(
        session,
        AuditAction.configuration_deleted,
        actor=actor,
        summary=f"Removed the configuration '{configuration.name}'",
        details={"name": configuration.name},
    )
    session.commit()


def _clear_picks(session: Session, user_id: str, configuration_id: str) -> None:
    """A pick that names the default, or a configuration about to go, means the default."""
    for document in session.exec(
        select(Document).where(
            Document.user_id == user_id, Document.configuration_id == configuration_id
        )
    ).all():
        document.configuration_id = None
        session.add(document)
    session.flush()


def to_input(configuration: AiConfiguration) -> ConfigurationInput:
    return ConfigurationInput(
        name=configuration.name,
        reading_method=configuration.reading_method,
        reading=StepInput(
            endpoint_id=configuration.reading_endpoint_id,
            model_name=configuration.reading_model_name,
            system_prompt=configuration.reading_system_prompt,
            instructions=configuration.reading_instructions,
            temperature=configuration.reading_temperature,
            max_tokens=configuration.reading_max_tokens,
            thinking=configuration.reading_thinking,
        ),
        filing_strategy=configuration.filing_strategy,
        filing=StepInput(
            endpoint_id=configuration.filing_endpoint_id,
            model_name=configuration.filing_model_name,
            system_prompt=configuration.filing_system_prompt,
            instructions=configuration.filing_instructions,
            temperature=configuration.filing_temperature,
            max_tokens=configuration.filing_max_tokens,
            thinking=configuration.filing_thinking,
        ),
        folder_system_prompt=configuration.folder_system_prompt,
        max_examples=configuration.max_examples,
        max_folders=configuration.max_folders,
        max_text_chars=configuration.max_text_chars,
    )


def _apply(configuration: AiConfiguration, data: ConfigurationInput) -> None:
    configuration.reading_method = data.reading_method
    configuration.reading_endpoint_id = data.reading.endpoint_id
    configuration.reading_model_name = data.reading.model_name
    configuration.reading_system_prompt = data.reading.system_prompt
    configuration.reading_instructions = data.reading.instructions
    configuration.reading_temperature = data.reading.temperature
    configuration.reading_max_tokens = data.reading.max_tokens
    configuration.reading_thinking = data.reading.thinking
    configuration.filing_endpoint_id = data.filing.endpoint_id
    configuration.filing_model_name = data.filing.model_name
    configuration.filing_strategy = data.filing_strategy
    configuration.filing_system_prompt = data.filing.system_prompt
    configuration.folder_system_prompt = data.folder_system_prompt
    configuration.filing_instructions = data.filing.instructions
    configuration.filing_temperature = data.filing.temperature
    configuration.filing_max_tokens = data.filing.max_tokens
    configuration.filing_thinking = data.filing.thinking
    configuration.max_examples = data.max_examples
    configuration.max_folders = data.max_folders
    configuration.max_text_chars = data.max_text_chars


def _describe(configuration: AiConfiguration) -> dict[str, object]:
    """For audit details: plain values, prompts by length rather than in full."""
    data = asdict(to_input(configuration))
    data.pop("name")
    for step in ("reading", "filing"):
        values = data[step]
        for key in ("system_prompt", "instructions"):
            text = values[key]
            values[key] = None if text is None else f"{len(text)} characters"
        values["thinking"] = str(values["thinking"])
    folder_prompt = data["folder_system_prompt"]
    data["folder_system_prompt"] = None if folder_prompt is None else f"{len(folder_prompt)} chars"
    data["reading_method"] = str(data["reading_method"])
    data["filing_strategy"] = str(data["filing_strategy"])
    return data


def _validated(
    session: Session, user_id: str, data: ConfigurationInput, *, configuration_id: str | None
) -> ConfigurationInput:
    name = data.name.strip()
    if not name:
        raise ValidationError("Give the configuration a name.")
    if len(name) > MAX_NAME_CHARS:
        raise ValidationError(f"The name must be {MAX_NAME_CHARS} characters or fewer.")
    clash = session.exec(
        select(AiConfiguration).where(
            AiConfiguration.user_id == user_id, AiConfiguration.name == name
        )
    ).first()
    if clash is not None and clash.id != configuration_id:
        raise ValidationError(f"There is already a configuration called '{name}'.")

    needs_model = data.reading_method == ReadingMethod.vision
    reading = _validated_step(session, user_id, data.reading, "Reading", needs_model=needs_model)
    filing = _validated_step(session, user_id, data.filing, "Filing", needs_model=True)
    return ConfigurationInput(
        name=name,
        reading_method=data.reading_method,
        reading=reading,
        filing_strategy=data.filing_strategy,
        filing=filing,
        folder_system_prompt=_prompt(data.folder_system_prompt, "Filing: the folder prompt"),
        max_examples=_in_range(data.max_examples, MAX_EXAMPLES_RANGE, "Examples"),
        max_folders=_in_range(data.max_folders, MAX_FOLDERS_RANGE, "Folders listed"),
        max_text_chars=_in_range(data.max_text_chars, MAX_TEXT_CHARS_RANGE, "Document text"),
    )


def _validated_step(
    session: Session, user_id: str, step: StepInput, label: str, *, needs_model: bool
) -> StepInput:
    endpoint_id: str | None = None
    model_name: str | None = None
    if needs_model:
        if not step.endpoint_id:
            raise ValidationError(f"{label}: choose a connection.")
        endpoint = connections.get_endpoint(session, user_id, step.endpoint_id)
        endpoint_id = endpoint.id
        model_name = (step.model_name or "").strip()
        if not model_name:
            raise ValidationError(f"{label}: choose a model.")
    low, high = TEMPERATURE_RANGE
    if not low <= step.temperature <= high:
        raise ValidationError(f"{label}: the temperature must be between {low:g} and {high:g}.")
    return StepInput(
        endpoint_id=endpoint_id,
        model_name=model_name,
        system_prompt=_prompt(step.system_prompt, f"{label}: the system prompt"),
        instructions=_instructions(step.instructions, label),
        temperature=step.temperature,
        max_tokens=_in_range(step.max_tokens, MAX_TOKENS_RANGE, f"{label}: the output limit"),
        thinking=step.thinking,
    )


def _prompt(text: str | None, label: str) -> str | None:
    """Blank means the built-in prompt; it is saved as None so it follows improvements."""
    if text is None or not text.strip():
        return None
    if len(text) > MAX_PROMPT_CHARS:
        raise ValidationError(f"{label} must be {MAX_PROMPT_CHARS:,} characters or fewer.")
    return text.strip()


def _instructions(text: str | None, label: str) -> str | None:
    if text is None or not text.strip():
        return None
    if len(text.strip()) > MAX_INSTRUCTIONS_CHARS:
        raise ValidationError(
            f"{label}: instructions must be {MAX_INSTRUCTIONS_CHARS} characters or fewer."
        )
    return text.strip()


def _in_range(value: int, bounds: tuple[int, int], label: str) -> int:
    low, high = bounds
    if not low <= value <= high:
        raise ValidationError(f"{label} must be between {low:,} and {high:,}.")
    return value


@dataclass(frozen=True)
class ReadingSetup:
    """Everything that changes what a reading produces, and the key it is stored under."""

    key: str
    method: ReadingMethod
    label: str


@dataclass(frozen=True)
class Plan:
    """A configuration resolved for one analysis: connections decrypted, prompts effective."""

    configuration_id: str
    configuration_name: str
    reading_method: ReadingMethod
    # None when the method needs no model, or when the connection has gone.
    reading_step: ResolvedStep | None
    reading_system_prompt: str
    reading_instructions: str | None
    reading_parameters: ai.TaskParameters
    filing_step: ResolvedStep | None
    filing_strategy: FilingStrategy
    filing_system_prompt: str
    folder_system_prompt: str
    filing_instructions: str | None
    filing_parameters: ai.TaskParameters
    max_examples: int
    max_folders: int
    max_text_chars: int

    def reading_setup(self, *, searchable: bool) -> ReadingSetup:
        """`searchable`: a searchable copy exists, so the text layer reads the OCR text."""
        if self.reading_method == ReadingMethod.text_layer:
            parts: dict[str, object] = {"method": "text_layer", "searchable": searchable}
            label = "Text layer"
        elif self.reading_method == ReadingMethod.tesseract:
            parts = {"method": "tesseract"}
            label = "Tesseract"
        else:
            step = self.reading_step
            parts = {
                "method": "vision",
                "endpoint_id": step.endpoint_id if step else None,
                "model": step.model_name if step else None,
                "system_prompt": self.reading_system_prompt,
                "instructions": self.reading_instructions,
                "temperature": self.reading_parameters.temperature,
                "max_tokens": self.reading_parameters.max_tokens,
                "thinking": str(self.reading_parameters.thinking),
            }
            label = step.label if step else "Vision model"
        digest = hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()
        return ReadingSetup(key=digest, method=self.reading_method, label=label)


def plan(session: Session, configuration: AiConfiguration) -> Plan:
    reading_step = None
    if configuration.reading_method == ReadingMethod.vision:
        reading_step = _step(
            session, configuration.reading_endpoint_id, configuration.reading_model_name
        )
    one_call = configuration.filing_strategy == FilingStrategy.one_call
    builtin_filing = BUILTIN.filing_one_call if one_call else BUILTIN.filing_fields
    return Plan(
        configuration_id=configuration.id,
        configuration_name=configuration.name,
        reading_method=configuration.reading_method,
        reading_step=reading_step,
        reading_system_prompt=configuration.reading_system_prompt or BUILTIN.reading,
        reading_instructions=configuration.reading_instructions,
        reading_parameters=ai.EXTRACTION_PARAMETERS.with_settings(
            configuration.reading_temperature,
            configuration.reading_max_tokens,
            configuration.reading_thinking,
        ),
        filing_step=_step(
            session, configuration.filing_endpoint_id, configuration.filing_model_name
        ),
        filing_strategy=configuration.filing_strategy,
        filing_system_prompt=configuration.filing_system_prompt or builtin_filing,
        folder_system_prompt=configuration.folder_system_prompt or BUILTIN.filing_folder,
        filing_instructions=configuration.filing_instructions,
        filing_parameters=ai.FILING_PARAMETERS.with_settings(
            configuration.filing_temperature,
            configuration.filing_max_tokens,
            configuration.filing_thinking,
        ),
        max_examples=configuration.max_examples,
        max_folders=configuration.max_folders,
        max_text_chars=configuration.max_text_chars,
    )


def _step(session: Session, endpoint_id: str | None, model_name: str | None) -> ResolvedStep | None:
    if endpoint_id is None or not model_name:
        return None
    endpoint = session.get(AiEndpoint, endpoint_id)
    if endpoint is None:
        return None
    return ResolvedStep.of(endpoint, model_name)


DOCUMENT_TEXT_PLACEHOLDER = (
    "[The document's text goes here: the reading, first {limit:,} characters.]"
)


@dataclass(frozen=True)
class PreviewCall:
    """What one call of a configuration is sent, the document left as a placeholder."""

    step: str
    model_label: str | None
    system_prompt: str | None
    user_prompt: str | None
    note: str


def preview(
    session: Session,
    webdav: WebDavService,
    app_settings: AppSettings,
    configuration: AiConfiguration,
) -> list[PreviewCall]:
    """Every call in the order an analysis makes them. Walks WebDAV for the folder tree."""
    resolved = plan(session, configuration)
    tree, samples = folders.prompt_context(webdav, app_settings)
    text = DOCUMENT_TEXT_PLACEHOLDER.format(limit=resolved.max_text_chars)
    calls = [_reading_preview(resolved)]
    filing_label = resolved.filing_step.label if resolved.filing_step else None
    folder_note = (
        "The folder tree is read from WebDAV when each document is filed, so it follows your "
        "folders as they change. Example filenames are listed only until a document has been "
        "approved; from then on, approved documents are the examples."
    )
    hint = app_settings.filename_pattern_hint
    known = knowledge.preview_text(app_settings)
    if resolved.filing_strategy == FilingStrategy.one_call:
        calls.append(
            PreviewCall(
                step="Filing",
                model_label=filing_label,
                system_prompt=resolved.filing_system_prompt,
                user_prompt=ai.build_prompt(
                    text,
                    tree,
                    samples,
                    hint,
                    resolved.filing_instructions,
                    max_folders=resolved.max_folders,
                    max_examples=resolved.max_examples,
                    knowledge=known,
                ),
                note=folder_note,
            )
        )
        return calls
    placeholder_fields = naming.NameFields(
        sender="[sender from the first call]", title="[title from the first call]"
    )
    calls += [
        PreviewCall(
            step="Fields",
            model_label=filing_label,
            system_prompt=resolved.filing_system_prompt,
            user_prompt=ai.build_prompt(
                text,
                tree,
                samples,
                hint,
                resolved.filing_instructions,
                with_folders=False,
                max_folders=resolved.max_folders,
                max_examples=resolved.max_examples,
                knowledge=known,
            ),
            note="The first of two calls: the name's fields, without the folder.",
        ),
        PreviewCall(
            step="Folder",
            model_label=filing_label,
            system_prompt=resolved.folder_system_prompt,
            user_prompt=ai.build_folder_prompt(
                text,
                tree,
                None,
                placeholder_fields,
                resolved.filing_instructions,
                max_folders=resolved.max_folders,
                knowledge=known,
            ),
            note="The second call, given what the first one found. " + folder_note,
        ),
    ]
    return calls


def _reading_preview(resolved: Plan) -> PreviewCall:
    if resolved.reading_method == ReadingMethod.text_layer:
        return PreviewCall(
            step="Reading",
            model_label=None,
            system_prompt=None,
            user_prompt=None,
            note=(
                "No model: the PDF's own text layer, or the searchable copy's when OCR made one."
            ),
        )
    if resolved.reading_method == ReadingMethod.tesseract:
        return PreviewCall(
            step="Reading",
            model_label=None,
            system_prompt=None,
            user_prompt=None,
            note=(
                f"No model: Tesseract reads the first {extraction.MAX_RENDERED_PAGES} pages, "
                "rendered as images."
            ),
        )
    return PreviewCall(
        step="Reading",
        model_label=resolved.reading_step.label if resolved.reading_step else None,
        system_prompt=resolved.reading_system_prompt,
        user_prompt=transcribe.user_prompt(resolved.reading_instructions),
        note=(
            f"Sent with the first {extraction.MAX_RENDERED_PAGES} pages of the document as "
            f"images, {transcribe.PAGES_PER_REQUEST} per request."
        ),
    )
