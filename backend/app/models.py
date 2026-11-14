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


class AiJsonMode(enum.StrEnum):
    """How filing asks an endpoint for JSON (SPEC 6.3). Names equal values, since SQLAlchemy
    stores enum names."""

    auto = "auto"
    json_schema = "json_schema"
    json_object = "json_object"
    none = "none"


class ReadingMethod(enum.StrEnum):
    """How a configuration turns a document into text (SPEC 6.3a)."""

    text_layer = "text_layer"
    tesseract = "tesseract"
    vision = "vision"


class FilingStrategy(enum.StrEnum):
    """Fields and folder in one reply, or fields first and the folder in a second call."""

    one_call = "one_call"
    two_calls = "two_calls"


class AiThinking(enum.StrEnum):
    """What a step asks of a thinking model, sent as reasoning_effort (SPEC 6.3b)."""

    default = "default"
    off = "off"
    low = "low"
    medium = "medium"
    high = "high"


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
    configuration_created = "configuration.created"
    configuration_updated = "configuration.updated"
    configuration_deleted = "configuration.deleted"
    configuration_defaulted = "configuration.defaulted"
    knowledge_updated = "knowledge.updated"
    knowledge_learned = "knowledge.learned"
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
    # A configuration picked for this document (SPEC 6.3a); None means the user's default.
    configuration_id: str | None = Field(default=None, foreign_key="ai_configuration.id")
    # The reading the latest analysis filed from, for the review screen. Not a foreign key:
    # reading.document_id already points the other way, and a cycle is not worth the check.
    reading_id: str | None = None
    # Why a pending analysis is not running: a connection it needs is unreachable, or there
    # is no configuration yet. None while it simply has not had its turn.
    waiting_reason: str | None = None
    # Tokens spent by the most recent analysis (ai.UsageSummary.to_json). On the document
    # rather than the proposal, because a failed analysis spends tokens too and has no proposal.
    analysis_usage: dict[str, object] | None = Field(default=None, sa_column=Column(JSON))


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
    # The name's fields (SPEC 7.1a); suggested_name is assembled from them and document_date.
    # All null on a proposal from before fields existed: its name is a whole, free name.
    sender: str | None = None
    account: str | None = None
    title: str | None = None
    period: str | None = None
    number: str | None = None
    # The exact user message built for this proposal (folder tree, sample filenames, naming
    # hint, document text) -- SPEC 8.3.5a. Nullable because proposals stored before this field
    # existed have nothing to backfill it with.
    prompt_text: str | None = None
    # The system prompt(s) actually sent. None on proposals from before configurations, all of
    # which were sent the built-in one.
    system_prompt: str | None = None
    # What produced it, for the chip on the review screen. Copies, not foreign keys: the
    # connection or configuration may be renamed or removed later, and the chip should keep
    # saying what produced this text. Nullable for proposals stored before these existed.
    endpoint_name: str | None = None
    configuration_id: str | None = None
    configuration_name: str | None = None
    reading_label: str | None = None


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
    # The approved name fields, kept as examples for later proposals; None for a hand-written
    # name, which has no fields, and for a trash.
    name_fields: dict[str, object] | None = Field(default=None, sa_column=Column(JSON))
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
    # Documents analysed per poll tick. Each tick reads its whole batch before filing any of
    # it, so on a GPU that holds one model at a time this is how many documents share one
    # model load; larger also means a single tick holds the queue longer.
    proposal_batch_size: int = Field(default=10, sa_column_kwargs={"server_default": "10"})
    # Archive knowledge (SPEC 6.3c): the order of the name's fields, and the owner's rules for
    # naming and filing that every configuration is given.
    name_template: list[str] = Field(
        default_factory=lambda: ["date", "sender", "account", "title", "period", "number"],
        sa_column=Column(JSON, nullable=False),
    )
    filing_rules: str | None = None


class KnownSender(SQLModel, table=True):
    """A sender short name the owner confirmed on approving (SPEC 6.3c). Never entered
    beforehand: learning only from approvals keeps the list to senders that really occur."""

    __tablename__ = "known_sender"

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    short_name: str
    # How the sender is written in documents, e.g. its legal name; matched against the text.
    aliases: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    created_at: datetime


class KnownAccount(SQLModel, table=True):
    """An account the owner holds with a known sender, as it goes into the name."""

    __tablename__ = "known_account"

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    sender_id: str = Field(foreign_key="known_sender.id", index=True)
    value: str
    created_at: datetime


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
    # Tokens the server holds per request, prompt and reply together (SPEC 6.3b). Entered by
    # hand to match the server: an OpenAI-compatible API cannot be asked for it, and Ollama
    # cuts an overlong prompt silently instead of refusing it. None means unknown, unchecked.
    context_limit: int | None = None
    json_mode: AiJsonMode = Field(
        default=AiJsonMode.auto, sa_column_kwargs={"server_default": "auto"}
    )
    # Per million tokens, per model, in whatever currency the provider bills; not secrets.
    # A list of {model_name, price_input_per_million, price_output_per_million}: models on one
    # connection are billed differently, and a model without an entry shows no cost.
    model_prices: list[dict[str, object]] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False, server_default="[]")
    )
    created_at: datetime


class AiConfiguration(SQLModel, table=True):
    """A named way of analysing a document: a reading step and a filing step (SPEC 6.3a).

    The two steps are columns rather than child rows: there are always exactly two, and a
    configuration is edited, duplicated and compared as a whole.
    """

    __tablename__ = "ai_configuration"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_ai_configuration_user_name"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    name: str
    # Exactly one per user who has any; enforced in services/configurations.py.
    is_default: bool = False

    reading_method: ReadingMethod = ReadingMethod.text_layer
    reading_endpoint_id: str | None = Field(default=None, foreign_key="ai_endpoint.id")
    reading_model_name: str | None = None
    # None means the built-in prompt, so improvements to it reach configurations nobody edited.
    reading_system_prompt: str | None = None
    reading_instructions: str | None = None
    reading_temperature: float = 0.1
    reading_max_tokens: int = 4096
    reading_thinking: AiThinking = AiThinking.default

    filing_endpoint_id: str | None = Field(default=None, foreign_key="ai_endpoint.id")
    filing_model_name: str | None = None
    filing_strategy: FilingStrategy = FilingStrategy.one_call
    filing_system_prompt: str | None = None
    folder_system_prompt: str | None = None
    filing_instructions: str | None = None
    filing_temperature: float = 0.1
    filing_max_tokens: int = 2048
    filing_thinking: AiThinking = AiThinking.default

    max_examples: int = 8
    max_folders: int = 250
    max_text_chars: int = 6000
    created_at: datetime


class Reading(SQLModel, table=True):
    """What one reading setup read off one document, kept so it is never read twice.

    `setup_key` hashes everything that changes the result (SPEC 6.3a); a configuration that
    differs only in its filing step shares the reading.
    """

    __tablename__ = "reading"
    __table_args__ = (
        UniqueConstraint("document_id", "setup_key", name="uq_reading_document_setup"),
    )

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    document_id: str = Field(foreign_key="document.id", index=True)
    setup_key: str
    method: ReadingMethod
    label: str
    text: str
    created_at: datetime


class LabResultStatus(enum.StrEnum):
    pending = "pending"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


class TestCaseSource(enum.StrEnum):
    done = "done"
    archive = "archive"


class TestSet(SQLModel, table=True):
    """A named list of filed documents whose right answer is known (SPEC 6.4a)."""

    __tablename__ = "test_set"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_test_set_user_name"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    name: str
    created_at: datetime


class TestCase(SQLModel, table=True):
    """One file in a test set and its right answer, copied in when added: a later revert or
    rename of the source must not change what a run is scored against."""

    __tablename__ = "test_case"
    __table_args__ = (UniqueConstraint("test_set_id", "path", name="uq_test_case_set_path"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    test_set_id: str = Field(foreign_key="test_set.id", index=True)
    source: TestCaseSource
    document_id: str | None = None
    path: str
    original_filename: str
    mime_type: str
    expected_name: str
    expected_date: date | None = None
    # None for a whole, hand-written name: then the name is scored as one value.
    expected_fields: dict[str, object] | None = Field(default=None, sa_column=Column(JSON))
    expected_folder: str
    created_at: datetime


class LabReading(SQLModel, table=True):
    """What one reading setup read off one test case. Kept apart from `reading`, which
    belongs to documents: an archive file in a test set has no document."""

    __tablename__ = "lab_reading"
    __table_args__ = (
        UniqueConstraint("test_case_id", "setup_key", name="uq_lab_reading_case_setup"),
    )

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    test_case_id: str = Field(foreign_key="test_case.id", index=True)
    setup_key: str
    label: str
    text: str
    created_at: datetime


class LabRun(SQLModel, table=True):
    """One test set run against configurations. Names are copies: a run outlives renames and
    deletions of what it ran."""

    __tablename__ = "lab_run"

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    test_set_id: str
    test_set_name: str
    configurations: list[dict[str, str]] = Field(sa_column=Column(JSON))
    cancelled: bool = False
    created_at: datetime
    finished_at: datetime | None = None


class LabResult(SQLModel, table=True):
    """One test case filed by one configuration in one run, with its scores."""

    __tablename__ = "lab_result"

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    run_id: str = Field(foreign_key="lab_run.id", index=True)
    test_case_id: str
    position: int
    configuration_id: str
    configuration_name: str
    original_filename: str
    expected_name: str
    expected_date: date | None = None
    expected_fields: dict[str, object] | None = Field(default=None, sa_column=Column(JSON))
    expected_folder: str
    status: LabResultStatus = LabResultStatus.pending
    waiting_reason: str | None = None
    error: str | None = None
    predicted_name: str | None = None
    predicted_date: date | None = None
    predicted_fields: dict[str, object] | None = Field(default=None, sa_column=Column(JSON))
    predicted_folder: str | None = None
    reading_label: str | None = None
    duration_ms: int | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float | None = None
    verdicts: dict[str, float] = Field(default_factory=dict, sa_column=Column(JSON))
    overrides: dict[str, float] = Field(default_factory=dict, sa_column=Column(JSON))
    finished_at: datetime | None = None


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
    # Priced at the time of the call, so a later price change does not rewrite history.
    cost: float | None = None


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
