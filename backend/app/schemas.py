from datetime import date, datetime

from pydantic import BaseModel, field_serializer, model_validator

from app.models import (
    AiJsonMode,
    AiTask,
    AuditEvent,
    Document,
    DocumentStatus,
    HistoryAction,
    HistoryEntry,
    OcrStatus,
    ProposalStatus,
)
from app.services.ai import SYSTEM_PROMPT, UsageSummary
from app.services.extraction import extension_of
from app.services.times import from_storage


class NameFieldsBody(BaseModel):
    """SPEC 7.1a. Optional fields are null; the service trims and checks every one."""

    sender: str
    account: str | None = None
    title: str
    period: str | None = None
    number: str | None = None


class AIProposalRead(BaseModel):
    suggested_name: str
    # None for a proposal from before fields existed; its name is a whole, free name.
    fields: NameFieldsBody | None = None
    target_folder_path: str
    document_date: date | None
    confidence_score: float
    reasoning_text: str
    model_name: str
    connection_name: str | None = None
    chosen_by_user: bool = False
    fallback_from: str | None = None
    # SPEC 8.3.5a: what was actually sent for this proposal, so the review screen can show it.
    # prompt_text is None for proposals stored before this field existed, and for the
    # /compare endpoint's in-memory results, which are never persisted. system_prompt has a
    # default because it is the same constant for every proposal, never per-row state.
    prompt_text: str | None = None
    system_prompt: str = SYSTEM_PROMPT


class StageUsageRead(BaseModel):
    model_label: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    calls: int
    unreported_calls: int


class UsageSummaryRead(BaseModel):
    """Tokens one analysis spent, per task. A task that made no calls is null."""

    extraction: StageUsageRead | None
    filing: StageUsageRead | None
    total_tokens: int

    @classmethod
    def from_summary(cls, summary: UsageSummary | None) -> "UsageSummaryRead | None":
        return cls.model_validate(summary.to_json()) if summary else None


class DocumentRead(BaseModel):
    id: str
    # What the analysis is doing right now, while the poller works on it; else None.
    analysis_stage: str | None = None
    original_filename: str
    extension: str
    mime_type: str
    file_size_bytes: int
    page_count: int | None
    scanned_at: datetime
    status: DocumentStatus
    skip_count: int
    proposal_status: ProposalStatus
    proposal: AIProposalRead | None
    proposal_error: str | None
    ocr_status: OcrStatus
    ocr_error: str | None
    analysis_usage: UsageSummaryRead | None = None
    # What the extraction task read off the pages, so the person can see what filing saw.
    extracted_markdown: str | None = None
    extraction_model: str | None = None
    extraction_error: str | None = None

    @field_serializer("scanned_at")
    def _serialize_scanned_at(self, value: datetime) -> str:
        """SQLite drops tzinfo, so re-attach UTC on the way out.

        Without this the frontend would read a UTC instant as local time.
        """
        return from_storage(value).isoformat()

    @classmethod
    def from_models(
        cls,
        document: Document,
        proposal: AIProposalRead | None,
        analysis_stage: str | None = None,
    ) -> "DocumentRead":
        return cls(
            analysis_stage=analysis_stage,
            id=document.id,
            original_filename=document.original_filename,
            extension=extension_of(document.original_filename),
            mime_type=document.mime_type,
            file_size_bytes=document.file_size_bytes,
            page_count=document.page_count,
            scanned_at=document.scanned_at,
            status=document.status,
            skip_count=document.skip_count,
            proposal_status=document.proposal_status,
            proposal=proposal,
            proposal_error=document.proposal_error,
            ocr_status=document.ocr_status,
            ocr_error=document.ocr_error,
            extracted_markdown=document.extracted_markdown,
            extraction_model=document.extraction_model,
            extraction_error=document.extraction_error,
            analysis_usage=(
                UsageSummaryRead.model_validate(document.analysis_usage)
                if document.analysis_usage
                else None
            ),
        )


class QueueResponse(BaseModel):
    items: list[DocumentRead]
    total_pending: int


class RegenerateRequest(BaseModel):
    """Optional: run this one analysis on a chosen connection and model.

    Both or neither -- a model name means nothing without the connection that serves it.
    """

    endpoint_id: str | None = None
    model_name: str | None = None

    @model_validator(mode="after")
    def _both_or_neither(self) -> "RegenerateRequest":
        if (self.endpoint_id is None) != (self.model_name is None):
            raise ValueError("Choose a connection and a model together.")
        if self.model_name is not None and not self.model_name.strip():
            raise ValueError("Choose a model.")
        return self


class RetriedResponse(BaseModel):
    retried: int


class MethodResultRead(BaseModel):
    """One way of reading a document, and what it proposed."""

    method: str  # "text_layer" | "ocr" | "markdown"
    model_name: str
    label: str
    text_preview: str
    proposal: AIProposalRead | None
    error: str | None
    duration_ms: int
    usage: UsageSummaryRead | None = None


class CompareResponse(BaseModel):
    results: list[MethodResultRead]


class DocumentResponse(BaseModel):
    document: DocumentRead


class HealthResponse(BaseModel):
    status: str
    # Whether the signed-in user has connected a Nextcloud. False without a session.
    webdav_configured: bool
    # None when there is nothing to probe: no session, or no connection yet.
    webdav_reachable: bool | None
    queue_depth: int


class AuthConfig(BaseModel):
    """What the sign-in screen needs before anyone is signed in."""

    oidc_enabled: bool
    oidc_provider_name: str
    registration_open: bool
    has_users: bool  # false = fresh instance, so the screen offers "create the first account"


class UserRead(BaseModel):
    id: str
    email: str
    is_admin: bool


class CredentialsRequest(BaseModel):
    email: str
    password: str


class SettingsRead(BaseModel):
    allowed_root_folders: list[str]
    trash_folder_path: str
    watch_folder_path: str
    filename_pattern: str | None
    filename_pattern_hint: str | None
    store_ocr_text: bool
    filing_instructions: str | None
    extraction_instructions: str | None
    proposal_batch_size: int


class SettingsUpdate(BaseModel):
    allowed_root_folders: list[str]
    trash_folder_path: str
    watch_folder_path: str
    filename_pattern: str | None = None
    filename_pattern_hint: str | None = None
    store_ocr_text: bool = True
    filing_instructions: str | None = None
    extraction_instructions: str | None = None
    proposal_batch_size: int = 10


class NextcloudRead(BaseModel):
    """CLAUDE.md rule 5: the password never leaves the backend, only whether one is set."""

    base_url: str
    username: str
    password_set: bool


class NextcloudWrite(BaseModel):
    base_url: str
    username: str
    # Blank keeps the stored password.
    password: str | None = None


class AiEndpointRead(BaseModel):
    """CLAUDE.md rule 5: the key itself never leaves the backend, only whether one is set."""

    id: str
    name: str
    base_url: str
    api_key_set: bool
    timeout_seconds: int
    context_limit: int | None
    json_mode: AiJsonMode
    price_input_per_million: float | None
    price_output_per_million: float | None
    used_by: list[AiTask]


class AiEndpointWrite(BaseModel):
    name: str
    base_url: str
    # Omitted or empty on an update means "keep the stored key", because the form is never
    # given the key back and so cannot send it again.
    api_key: str | None = None
    # Omitted keeps the stored value (or the default, for a new endpoint).
    timeout_seconds: int | None = None
    # Replaced as sent, like the rest of a PUT: the form always sends all four, and null is
    # how it says "no limit known" or "no price".
    context_limit: int | None = None
    json_mode: AiJsonMode = AiJsonMode.auto
    price_input_per_million: float | None = None
    price_output_per_million: float | None = None


class AiTaskStepWrite(BaseModel):
    endpoint_id: str
    model_name: str


class AiTaskStepRead(BaseModel):
    endpoint_id: str
    endpoint_name: str
    model_name: str


class AiTaskChainRead(BaseModel):
    """A task's endpoints in the order they are tried."""

    task: AiTask
    steps: list[AiTaskStepRead]


class AiTaskChainUpdate(BaseModel):
    steps: list[AiTaskStepWrite]


class AiModelsRequest(BaseModel):
    """The endpoint under test may be one being typed into the add form, so it is described
    by URL rather than by id. A blank key on a saved endpoint means "use the stored one"."""

    base_url: str
    api_key: str | None = None
    endpoint_id: str | None = None


class AiModelsResponse(BaseModel):
    models: list[str]


class AiTaskContextRead(BaseModel):
    """What one task is sent, built from the current settings with a placeholder document."""

    task: AiTask
    chain: list[str]
    system_prompt: str
    user_prompt: str
    note: str


class ApproveRequest(BaseModel):
    """SPEC 5 deliberately omits the original suggestion: the backend already has the
    proposal and computes `was_overridden` itself, so the client cannot misreport it."""

    # Exactly one of these two (SPEC 5): fields the backend assembles, or a hand-written name.
    name_fields: NameFieldsBody | None = None
    final_name: str | None = None
    final_folder_path: str
    document_date: date | None = None


class HistoryEntryRead(BaseModel):
    id: str
    document_id: str
    original_filename: str
    final_filename: str
    final_folder_path: str
    action: HistoryAction
    was_overridden: bool
    processed_at: datetime
    revertible: bool

    @field_serializer("processed_at")
    def _serialize_processed_at(self, value: datetime) -> str:
        return from_storage(value).isoformat()

    @classmethod
    def from_model(cls, entry: HistoryEntry) -> "HistoryEntryRead":
        return cls(
            id=entry.id,
            document_id=entry.document_id,
            original_filename=entry.original_filename,
            final_filename=entry.final_filename,
            final_folder_path=entry.final_folder_path,
            action=entry.action,
            was_overridden=entry.was_overridden,
            processed_at=entry.processed_at,
            revertible=entry.revertible,
        )


class RoutedResponse(BaseModel):
    document: DocumentRead
    history_entry: HistoryEntryRead


class HistoryPage(BaseModel):
    items: list[HistoryEntryRead]
    next_cursor: str | None


class AuditEventRead(BaseModel):
    id: str
    occurred_at: datetime
    actor_label: str
    action: str
    document_id: str | None
    summary: str
    details: dict[str, object]
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None

    @field_serializer("occurred_at")
    def _serialize_occurred_at(self, value: datetime) -> str:
        return from_storage(value).isoformat()

    @classmethod
    def from_model(cls, event: AuditEvent) -> "AuditEventRead":
        return cls(
            id=event.id,
            occurred_at=event.occurred_at,
            actor_label=event.actor_label,
            action=event.action,
            document_id=event.document_id,
            summary=event.summary,
            details=event.details or {},
            prompt_tokens=event.prompt_tokens,
            completion_tokens=event.completion_tokens,
            total_tokens=event.total_tokens,
        )


class AuditPage(BaseModel):
    items: list[AuditEventRead]
    next_cursor: str | None


class ModelUsageRead(BaseModel):
    task: str
    model_label: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    calls: int


class AuditUsageRead(BaseModel):
    """Tokens spent over the window, from analyses and comparisons."""

    days: int
    analyses: int
    comparisons: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    by_model: list[ModelUsageRead]


class AiUsageRowRead(BaseModel):
    """One day, connection, model and feature, summed. See services/ai_usage.py."""

    day: str
    endpoint_id: str | None
    connection: str
    connection_exists: bool
    model: str
    model_configured: bool
    feature: str
    requests: int
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    without_usage: int
    failed: int
    fallback: int
    duration_ms: int
    timed: int
    cost: float | None


class AiUsageRead(BaseModel):
    rows: list[AiUsageRowRead]
    analyses: int
    comparisons: int


class RevertResponse(BaseModel):
    history_entry: HistoryEntryRead
    document: DocumentRead


class FolderNode(BaseModel):
    path: str
    name: str
    has_children: bool
    children: list["FolderNode"] | None
    file_count: int
    # Newest first, capped; the picker shows them for the highlighted folder.
    recent_files: list[str]


class CreateFolderRequest(BaseModel):
    parent_path: str
    name: str


class SiblingFile(BaseModel):
    filename: str
    created_at: str | None
    size_bytes: int


class FolderContext(BaseModel):
    path: str
    exists: bool
    siblings: list[SiblingFile]
    total_file_count: int
    filename_collision: bool
