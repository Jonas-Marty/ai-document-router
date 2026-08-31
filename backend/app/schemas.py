from datetime import date, datetime

from pydantic import BaseModel, field_serializer

from app.models import (
    Document,
    DocumentStatus,
    HistoryAction,
    HistoryEntry,
    OcrStatus,
    ProposalStatus,
)
from app.services.extraction import extension_of
from app.services.times import from_storage


class AIProposalRead(BaseModel):
    suggested_name: str
    target_folder_path: str
    document_date: date | None
    confidence_score: float
    reasoning_text: str
    model_name: str


class DocumentRead(BaseModel):
    id: str
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

    @field_serializer("scanned_at")
    def _serialize_scanned_at(self, value: datetime) -> str:
        """SQLite drops tzinfo, so re-attach UTC on the way out.

        Without this the frontend would read a UTC instant as local time.
        """
        return from_storage(value).isoformat()

    @classmethod
    def from_models(cls, document: Document, proposal: AIProposalRead | None) -> "DocumentRead":
        return cls(
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
        )


class QueueResponse(BaseModel):
    items: list[DocumentRead]
    total_pending: int


class RetriedResponse(BaseModel):
    retried: int


class MethodResultRead(BaseModel):
    """One way of reading a document, and what it proposed."""

    method: str  # "text_layer" | "ocr" | "vision"
    model_name: str
    label: str
    text_preview: str
    proposal: AIProposalRead | None
    error: str | None
    duration_ms: int


class CompareResponse(BaseModel):
    results: list[MethodResultRead]


class DocumentResponse(BaseModel):
    document: DocumentRead


class HealthResponse(BaseModel):
    status: str
    webdav_reachable: bool
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
    filename_pattern: str | None
    filename_pattern_hint: str | None
    ai_endpoint_url: str
    ai_model_name: str
    vision_model_names: list[str]
    store_ocr_text: bool
    ai_api_key_set: bool


class SettingsUpdate(BaseModel):
    allowed_root_folders: list[str]
    trash_folder_path: str
    filename_pattern: str | None = None
    filename_pattern_hint: str | None = None
    ai_endpoint_url: str
    ai_model_name: str
    vision_model_names: list[str] = []
    store_ocr_text: bool = True
    ai_api_key: str | None = None


class AiModelsRequest(BaseModel):
    """The endpoint under test is the one typed into the form, which may not be saved yet."""

    ai_endpoint_url: str
    ai_api_key: str | None = None  # omitted or empty = test with the stored key


class AiModelsResponse(BaseModel):
    models: list[str]


class ApproveRequest(BaseModel):
    """SPEC 5 deliberately omits the original suggestion: the backend already has the
    proposal and computes `was_overridden` itself, so the client cannot misreport it."""

    final_name: str
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


class RevertResponse(BaseModel):
    history_entry: HistoryEntryRead
    document: DocumentRead


class FolderNode(BaseModel):
    path: str
    name: str
    has_children: bool
    children: list["FolderNode"] | None
    file_count: int


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
