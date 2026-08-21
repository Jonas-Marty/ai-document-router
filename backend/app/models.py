import enum
from datetime import date, datetime
from uuid import uuid4

from sqlalchemy import JSON, Column, LargeBinary
from sqlmodel import Field, SQLModel


def _uuid() -> str:
    return str(uuid4())


class DocumentStatus(enum.StrEnum):
    pending = "pending"
    skipped = "skipped"
    moved = "moved"
    trashed = "trashed"
    failed = "failed"


class ProposalStatus(enum.StrEnum):
    pending = "pending"
    ready = "ready"
    failed = "failed"


class HistoryAction(enum.StrEnum):
    moved = "moved"
    trashed = "trashed"


class Document(SQLModel, table=True):
    __tablename__ = "document"

    id: str = Field(default_factory=_uuid, primary_key=True)
    webdav_path: str = Field(unique=True, index=True)
    original_filename: str
    mime_type: str
    file_size_bytes: int
    page_count: int | None = None
    content_hash: str
    scanned_at: datetime
    discovered_at: datetime
    status: DocumentStatus = DocumentStatus.pending
    skip_count: int = 0
    proposal_status: ProposalStatus = ProposalStatus.pending
    proposal_error: str | None = None
    error_message: str | None = None


class Proposal(SQLModel, table=True):
    __tablename__ = "proposal"

    id: str = Field(default_factory=_uuid, primary_key=True)
    document_id: str = Field(foreign_key="document.id", unique=True, index=True)
    suggested_name: str
    target_folder_path: str
    document_date: date | None = None
    confidence_score: float
    reasoning_text: str
    model_name: str
    created_at: datetime


class HistoryEntry(SQLModel, table=True):
    __tablename__ = "history_entry"

    id: str = Field(default_factory=_uuid, primary_key=True)
    document_id: str = Field(foreign_key="document.id", index=True)
    original_filename: str
    final_filename: str
    final_folder_path: str
    source_folder_path: str
    action: HistoryAction
    was_overridden: bool
    suggestion_snapshot: dict[str, object] = Field(sa_column=Column(JSON))
    processed_at: datetime
    revertible: bool


class AppSettings(SQLModel, table=True):
    __tablename__ = "app_settings"

    id: int = Field(default=1, primary_key=True)
    allowed_root_folders: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    trash_folder_path: str = ""
    filename_pattern: str | None = None
    filename_pattern_hint: str | None = None
    ai_endpoint_url: str = ""
    ai_model_name: str = ""
    ai_api_key_encrypted: bytes | None = Field(default=None, sa_column=Column(LargeBinary))
