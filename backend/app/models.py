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


class OcrStatus(enum.StrEnum):
    """Whether a searchable copy of a document exists, is coming, or was never needed.

    `not_needed` covers both "this already has a text layer" and "this is not a PDF", which
    are the same thing from here: there is nothing to add and nothing to wait for.
    """

    not_needed = "not_needed"
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
    ocr_status: OcrStatus = OcrStatus.not_needed
    ocr_error: str | None = None
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
    # The exact user message built for this proposal (folder tree, sample filenames, naming
    # hint, document text) -- SPEC 8.3.5a. Nullable because proposals stored before this field
    # existed have nothing to backfill it with.
    prompt_text: str | None = None


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
    # Extra models offered on the review screen's method comparison, read from the same
    # endpoint and key as ai_model_name. Empty means "don't offer a vision comparison";
    # nothing here ever runs on a poller tick.
    vision_model_names: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    # When a scanned document has no text layer, OCR one in and file that copy instead of
    # the original. Settable rather than always-on because it is the only thing in this app
    # that writes *content* to WebDAV, and the person whose archive it is should be able to
    # stop it without redeploying.
    store_ocr_text: bool = True
    ai_api_key_encrypted: bytes | None = Field(default=None, sa_column=Column(LargeBinary))


class User(SQLModel, table=True):
    """A person who can sign in. Password and OIDC are two ways into the same row.

    `password_hash` is None for an account that only ever signed in through the identity
    provider, and `oidc_subject` is None for one that only ever used a password. An account
    can carry both: signing in with OIDC using the email of an existing local account links
    the two rather than creating a second row.
    """

    __tablename__ = "user"

    id: str = Field(default_factory=_uuid, primary_key=True)
    email: str = Field(unique=True, index=True)
    password_hash: str | None = None
    oidc_subject: str | None = Field(default=None, unique=True, index=True)
    is_admin: bool = False
    created_at: datetime
    last_login_at: datetime | None = None


class UserSession(SQLModel, table=True):
    """A signed-in browser. `id` is the SHA-256 of the cookie value, never the value itself,
    so a copy of the database does not hand out live sessions."""

    __tablename__ = "user_session"

    id: str = Field(primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime


class OidcLogin(SQLModel, table=True):
    """One in-flight authorization code flow: the PKCE verifier and nonce, keyed by state.

    Server-side rather than in a cookie because the verifier must never reach the browser,
    and rows are single-use -- the callback deletes the row it consumes.
    """

    __tablename__ = "oidc_login"

    state: str = Field(primary_key=True)
    code_verifier: str
    nonce: str
    redirect_uri: str
    created_at: datetime
