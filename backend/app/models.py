import enum
from datetime import date, datetime
from uuid import uuid4

from sqlalchemy import JSON, Column, LargeBinary, UniqueConstraint
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


class AiTask(enum.StrEnum):
    """The two jobs a model can be given, in the order they run.

    Split because they want different models: reading a page well is a vision problem, and
    choosing a filename from what was read is a text one. Keeping them separate is what lets
    the reading happen on a local box and the choosing happen anywhere.
    """

    extraction = "extraction"
    filing = "filing"


class AuditAction(enum.StrEnum):
    """Everything the audit log records (SPEC 6.7). Dotted so a category is a prefix."""

    document_ingested = "document.ingested"
    document_analyzed = "document.analyzed"
    document_compared = "document.compared"
    document_ocr_completed = "document.ocr_completed"
    document_ocr_failed = "document.ocr_failed"
    document_regenerate_requested = "document.regenerate_requested"
    documents_retry_failed = "documents.retry_failed"
    document_approved = "document.approved"
    document_skipped = "document.skipped"
    document_trashed = "document.trashed"
    history_reverted = "history.reverted"
    settings_updated = "settings.updated"
    ai_endpoint_created = "ai_endpoint.created"
    ai_endpoint_updated = "ai_endpoint.updated"
    ai_endpoint_deleted = "ai_endpoint.deleted"
    ai_task_updated = "ai_task.updated"
    auth_registered = "auth.registered"
    auth_login = "auth.login"
    auth_login_failed = "auth.login_failed"
    auth_logout = "auth.logout"


class Document(SQLModel, table=True):
    __tablename__ = "document"
    # Unique per owner, not globally: two people's Nextclouds can both hold /Scans/Inbox/scan.pdf.
    __table_args__ = (UniqueConstraint("user_id", "webdav_path", name="uq_document_user_path"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    # Whose Nextcloud the file is on. Every read and write is scoped by it (SPEC 6.5).
    user_id: str = Field(foreign_key="user.id", index=True)
    webdav_path: str = Field(index=True)
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
    # What the extraction task read off the pages, kept so re-running the filing task (a
    # regenerate, a changed folder list) does not pay for the pages to be read again.
    extracted_markdown: str | None = None
    extraction_model: str | None = None
    # Set when extraction was configured and still failed. Not fatal on its own: filing falls
    # back to the PDF's own text layer, and this says why the better input is missing.
    extraction_error: str | None = None
    # Tokens spent by the most recent analysis (ai.UsageSummary.to_json). On the document
    # rather than the proposal, because a failed analysis spends tokens too and has no proposal.
    analysis_usage: dict[str, object] | None = Field(default=None, sa_column=Column(JSON))
    # A connection and model picked for the next analysis only. The poller consumes and clears
    # them; a plain re-run leaves them empty and uses the configured filing chain.
    rerun_endpoint_id: str | None = None
    rerun_model_name: str | None = None


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
    """One user's settings, their Nextcloud connection included. A row per user (SPEC 6.5)."""

    __tablename__ = "app_settings"

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(foreign_key="user.id", unique=True, index=True)
    # The user's own Nextcloud: the WebDAV files URL, their login, and an app password.
    # The password is Fernet-encrypted with SECRET_KEY and never returned (CLAUDE.md rule 5).
    webdav_base_url: str = ""
    webdav_username: str = ""
    webdav_password_encrypted: bytes | None = Field(default=None, sa_column=Column(LargeBinary))
    allowed_root_folders: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    trash_folder_path: str = ""
    # Where the scanner drops new files. A setting rather than env because it describes the
    # archive, like the roots and the trash; empty until set, and nothing is ingested before.
    watch_folder_path: str = ""
    filename_pattern: str | None = None
    filename_pattern_hint: str | None = None
    # When a scanned document has no text layer, OCR one in and file that copy instead of
    # the original. Settable rather than always-on because it is the only thing in this app
    # that writes *content* to WebDAV, and the person whose archive it is should be able to
    # stop it without redeploying.
    store_ocr_text: bool = True
    # The person's own words for each task, added to what the model is sent (SPEC 6.3, 6.3a).
    # Per task because they answer different questions: filing needs "insurance letters go
    # under /Versicherung", extraction needs "these are German, keep the umlauts".
    filing_instructions: str | None = None
    extraction_instructions: str | None = None
    # Documents analysed per poll tick. Each tick reads its whole batch before filing any of
    # it, so on a GPU that holds one model at a time this is how many documents share one
    # model load; larger also means a single tick holds the queue longer.
    proposal_batch_size: int = Field(default=10, sa_column_kwargs={"server_default": "10"})


class AiEndpoint(SQLModel, table=True):
    """One OpenAI-compatible server, named by the person who added it.

    Named rather than positional because these are chosen from lists in the UI and quoted
    back in failure messages: "Couldn't reach Local Ollama" is something you can act on,
    where a URL or an id is something you have to go look up first.
    """

    __tablename__ = "ai_endpoint"
    # Names are how a person tells their endpoints apart, so unique per person only.
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_ai_endpoint_user_name"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    name: str = Field(index=True)
    base_url: str
    api_key_encrypted: bytes | None = Field(default=None, sa_column=Column(LargeBinary))
    # How long to wait for a reply. Per endpoint because a hosted API answers in seconds
    # and a 14B model on a home GPU, loading cold and thinking first, can take minutes.
    timeout_seconds: int = Field(default=120, sa_column_kwargs={"server_default": "120"})
    created_at: datetime


class AiTaskStep(SQLModel, table=True):
    """One (endpoint, model) pair to try for a task, and when to try it.

    The rows for a task form a fallback chain in `position` order: a self-hosted model first,
    a hosted provider behind it, so a document still gets read when the machine at home is
    off. Both the endpoint and the model are per step -- the same endpoint can appear twice
    with different models, and the same model name can mean different things on two servers.
    """

    __tablename__ = "ai_task_step"

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    task: AiTask = Field(index=True)
    position: int
    endpoint_id: str = Field(foreign_key="ai_endpoint.id", index=True)
    model_name: str


class AuditEvent(SQLModel, table=True):
    """One thing that happened, who did it, and what it cost (SPEC 6.7).

    `actor_user_id` is deliberately not a foreign key, and `actor_label` keeps the email as it
    was: an audit trail has to outlive the account it names. `action` is a plain string
    rather than an enum column, because SQLAlchemy stores enum *names* and these values
    ("document.approved") are what the API and a person reading the table both expect.
    Token counts are typed columns, not buried in `details`, so they can be summed in SQL.
    """

    __tablename__ = "audit_event"

    id: str = Field(default_factory=_uuid, primary_key=True)
    occurred_at: datetime = Field(index=True)
    # Whose activity this is: the document's owner, or the actor for events with no document.
    # Separate from the actor because the poller acts on someone's documents as "system".
    # None only for events about no one, like a sign-in attempt for an unknown address.
    user_id: str | None = Field(default=None, index=True)
    actor_user_id: str | None = None
    actor_label: str
    action: str = Field(index=True)
    document_id: str | None = Field(default=None, index=True)
    summary: str
    details: dict[str, object] = Field(default_factory=dict, sa_column=Column(JSON))
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class AiCall(SQLModel, table=True):
    """Model calls, for the AI usage page: one row per call, or per stage of an analysis
    carried over from before calls were recorded one by one (`requests` > 1 then).

    Kept apart from audit_event so the figures survive audit retention, and so a period can
    be summed in SQL. `endpoint_id` is not a foreign key and `connection` keeps the name as
    it was: usage outlives the endpoint it ran on.
    """

    __tablename__ = "ai_call"

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(index=True)
    occurred_at: datetime = Field(index=True)
    # One analysis or one comparison; counts them without double-counting their calls.
    run_id: str = Field(index=True)
    document_id: str | None = None
    # extraction | filing | comparison
    feature: str
    endpoint_id: str | None = None
    connection: str
    model: str
    requests: int = 1
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    # Of `requests`: how many replied without a usage block, got no reply, ran as a fallback.
    without_usage: int = 0
    failed: int = 0
    fallback: int = 0
    # Summed over `timed` calls; carried-over rows have no durations.
    duration_ms: int = 0
    timed: int = 0


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
