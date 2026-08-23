from datetime import date, datetime

from pydantic import BaseModel, field_serializer

from app.models import Document, DocumentStatus, ProposalStatus
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
        )


class QueueResponse(BaseModel):
    items: list[DocumentRead]
    total_pending: int


class DocumentResponse(BaseModel):
    document: DocumentRead


class HealthResponse(BaseModel):
    status: str
    webdav_reachable: bool
    queue_depth: int


class SettingsRead(BaseModel):
    allowed_root_folders: list[str]
    trash_folder_path: str
    filename_pattern: str | None
    filename_pattern_hint: str | None
    ai_endpoint_url: str
    ai_model_name: str
    ai_api_key_set: bool


class SettingsUpdate(BaseModel):
    allowed_root_folders: list[str]
    trash_folder_path: str
    filename_pattern: str | None = None
    filename_pattern_hint: str | None = None
    ai_endpoint_url: str
    ai_model_name: str
    ai_api_key: str | None = None
