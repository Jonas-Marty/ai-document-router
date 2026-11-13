from datetime import date, datetime

from pydantic import BaseModel, Field, field_serializer

from app.models import (
    AiJsonMode,
    AiThinking,
    AuditEvent,
    Document,
    DocumentStatus,
    FilingStrategy,
    HistoryAction,
    HistoryEntry,
    OcrStatus,
    ProposalStatus,
    ReadingMethod,
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
    # What produced it (SPEC 6.3a); None on proposals from before configurations.
    configuration_name: str | None = None
    reading_label: str | None = None
    # SPEC 8.3.5a: what was actually sent for this proposal, so the review screen can show it.
    # prompt_text is None for proposals stored before this field existed, and for the
    # /compare endpoint's in-memory results, which are never persisted. system_prompt falls
    # back to the built-in one, which every proposal from before configurations was sent.
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
    # Why a pending analysis waits (SPEC 6.3a), and a configuration picked for this document.
    waiting_reason: str | None = None
    configuration_name: str | None = None
    # The reading the latest analysis filed from, so the person can see what filing saw.
    reading: "DocumentReadingRead | None" = None

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
        *,
        reading: "DocumentReadingRead | None" = None,
        configuration_name: str | None = None,
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
            waiting_reason=document.waiting_reason,
            configuration_name=configuration_name,
            reading=reading,
            analysis_usage=(
                UsageSummaryRead.model_validate(document.analysis_usage)
                if document.analysis_usage
                else None
            ),
        )


class DocumentReadingRead(BaseModel):
    label: str
    text: str


DocumentRead.model_rebuild()


class QueueResponse(BaseModel):
    items: list[DocumentRead]
    total_pending: int


class RegenerateRequest(BaseModel):
    """Optional: analyse with this configuration from now on (SPEC 6.3a)."""

    configuration_id: str | None = None


class RetriedResponse(BaseModel):
    retried: int


# --- Inbox (SPEC 6.4b) ---


class InboxCardRead(BaseModel):
    id: str
    original_filename: str
    name: str | None
    folder: str | None
    sender: str | None
    document_date: date | None
    scanned_at: datetime
    configuration_name: str | None
    proposal_status: ProposalStatus
    later: bool
    flags: list[str]
    analysis_stage: str | None
    waiting_reason: str | None
    error: str | None
    history_entry_id: str | None
    revertible: bool
    processed_at: datetime | None

    @field_serializer("scanned_at")
    def _serialize_scanned_at(self, value: datetime) -> str:
        return from_storage(value).isoformat()

    @field_serializer("processed_at")
    def _serialize_processed_at(self, value: datetime | None) -> str | None:
        return from_storage(value).isoformat() if value else None


class InboxCounts(BaseModel):
    analyse: int
    ready: int
    done: int
    trashed: int


class InboxPageRead(BaseModel):
    items: list[InboxCardRead]
    total: int
    counts: InboxCounts
    senders: list[str]
    folders: list[str]


class DocumentIdsRequest(BaseModel):
    document_ids: list[str] = Field(min_length=1, max_length=500)


class AnalyseRequest(DocumentIdsRequest):
    configuration_id: str | None = None


class EstimateRequest(DocumentIdsRequest):
    configuration_id: str


class AnalysedResponse(BaseModel):
    queued: int


class TrashFailureRead(BaseModel):
    id: str
    message: str


class BulkTrashResponse(BaseModel):
    trashed: int
    failed: list[TrashFailureRead]


class AnalysisEstimateRead(BaseModel):
    documents: int
    readings_needed: int
    prompt_tokens: int | None
    completion_tokens: int | None
    cost: float | None
    connections: list[str]


class EndpointStatusRead(BaseModel):
    id: str
    name: str
    reachable: bool


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
    proposal_batch_size: int


class SettingsUpdate(BaseModel):
    allowed_root_folders: list[str]
    trash_folder_path: str
    watch_folder_path: str
    filename_pattern: str | None = None
    filename_pattern_hint: str | None = None
    store_ocr_text: bool = True
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


class AiModelPrice(BaseModel):
    model_name: str
    price_input_per_million: float | None = None
    price_output_per_million: float | None = None


class AiEndpointRead(BaseModel):
    """CLAUDE.md rule 5: the key itself never leaves the backend, only whether one is set."""

    id: str
    name: str
    base_url: str
    api_key_set: bool
    timeout_seconds: int
    context_limit: int | None
    json_mode: AiJsonMode
    model_prices: list[AiModelPrice]
    # Names of the configurations that use it; it cannot be removed while any do.
    used_by: list[str]


class AiEndpointWrite(BaseModel):
    name: str
    base_url: str
    # Omitted or empty on an update means "keep the stored key", because the form is never
    # given the key back and so cannot send it again.
    api_key: str | None = None
    # Omitted keeps the stored value (or the default, for a new endpoint).
    timeout_seconds: int | None = None
    # Replaced as sent, like the rest of a PUT: the form always sends all of them, null is
    # how it says "no limit known", and the price list replaces the stored one whole.
    context_limit: int | None = None
    json_mode: AiJsonMode = AiJsonMode.auto
    model_prices: list[AiModelPrice] = []


class AiModelsRequest(BaseModel):
    """The endpoint under test may be one being typed into the add form, so it is described
    by URL rather than by id. A blank key on a saved endpoint means "use the stored one"."""

    base_url: str
    api_key: str | None = None
    endpoint_id: str | None = None


class AiModelsResponse(BaseModel):
    models: list[str]


class ConfigurationStepBody(BaseModel):
    """One step of a configuration. endpoint_name is filled in on the way out and ignored
    on the way in."""

    endpoint_id: str | None = None
    endpoint_name: str | None = None
    model_name: str | None = None
    system_prompt: str | None = None
    instructions: str | None = None
    temperature: float
    max_tokens: int
    thinking: AiThinking = AiThinking.default


class ConfigurationWrite(BaseModel):
    name: str
    reading_method: ReadingMethod
    reading: ConfigurationStepBody
    filing_strategy: FilingStrategy
    filing: ConfigurationStepBody
    folder_system_prompt: str | None = None
    max_examples: int
    max_folders: int
    max_text_chars: int


class ConfigurationRead(ConfigurationWrite):
    id: str
    is_default: bool


class BuiltinPromptsRead(BaseModel):
    reading: str
    filing_one_call: str
    filing_fields: str
    filing_folder: str


class ConfigurationPreviewRead(BaseModel):
    """What one call of a configuration is sent, the document left as a placeholder."""

    step: str
    model_label: str | None
    system_prompt: str | None
    user_prompt: str | None
    note: str


class ApproveRequest(BaseModel):
    """SPEC 5 deliberately omits the original suggestion: the backend already has the
    proposal and computes `was_overridden` itself, so the client cannot misreport it."""

    # Exactly one of these two (SPEC 5): fields the backend assembles, or a hand-written name.
    name_fields: NameFieldsBody | None = None
    final_name: str | None = None
    final_folder_path: str
    document_date: date | None = None
    # SPEC 6.4: learn the sender / the account with the approval. Only with name_fields.
    remember_sender: bool = False
    remember_account: bool = False


class ArchiveKnowledgeRead(BaseModel):
    name_template: list[str]
    filing_rules: str | None


class ArchiveKnowledgeWrite(BaseModel):
    name_template: list[str]
    filing_rules: str | None = None


class KnownAccountRead(BaseModel):
    id: str
    value: str


class KnownSenderRead(BaseModel):
    id: str
    short_name: str
    aliases: list[str]
    accounts: list[KnownAccountRead]


class KnownSenderWrite(BaseModel):
    short_name: str
    aliases: list[str] = []


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
