// Mirrors backend/app/schemas.py and backend/app/models.py exactly. Keys stay snake_case —
// no case-transform layer (CLAUDE.md).

export type DocumentStatus = "pending" | "skipped" | "moved" | "trashed" | "failed";
export type ProposalStatus = "pending" | "ready" | "failed";
/** `not_needed` covers both "already has a text layer" and "not a PDF": either way there is
 * nothing to add and nothing to wait for. */
export type OcrStatus = "not_needed" | "pending" | "ready" | "failed";
export type HistoryAction = "moved" | "trashed";

/** The name's parts besides the date (SPEC 7.1a). Optional ones are null, never "". */
export interface NameFields {
  sender: string;
  account: string | null;
  title: string;
  period: string | null;
  number: string | null;
}

export interface AIProposal {
  suggested_name: string; // no extension; assembled from document_date + fields
  fields: NameFields | null; // null: a whole name from before fields existed
  target_folder_path: string;
  document_date: string | null; // "YYYY-MM-DD"
  confidence_score: number; // 0.0-1.0
  reasoning_text: string; // plain text, may contain newlines -- not markdown
  model_name: string;
  // Who answered. connection_name is null for proposals stored before it was recorded.
  connection_name: string | null;
  // What produced it (SPEC 6.3a); null on proposals from before configurations.
  configuration_name: string | null;
  reading_label: string | null; // "Text layer", "Tesseract", or the vision model's label
  // What was actually sent for this proposal (SPEC 8.3.5a). prompt_text is null for
  // proposals stored before this field existed.
  prompt_text: string | null;
  system_prompt: string;
}

export interface Document {
  id: string;
  analysis_stage: string | null; // what the analysis is doing right now, while it runs
  original_filename: string;
  extension: string; // ".pdf", lowercase, includes the dot
  mime_type: string;
  file_size_bytes: number;
  page_count: number | null;
  scanned_at: string; // ISO 8601 with UTC offset
  status: DocumentStatus;
  skip_count: number;
  proposal_status: ProposalStatus;
  proposal: AIProposal | null;
  proposal_error: string | null;
  ocr_status: OcrStatus;
  ocr_error: string | null;
  analysis_usage: UsageSummary | null; // tokens the most recent analysis spent, success or not
  // Why a pending analysis is waiting, e.g. "Waiting for Tower to read"; null otherwise.
  waiting_reason: string | null;
  // The configuration picked for this document; null means it follows the default.
  configuration_name: string | null;
  reading: DocumentReading | null; // what the latest analysis filed from
}

export interface DocumentReading {
  label: string;
  text: string;
}

/** Every call one task made during one analysis, added up. */
export interface StageUsage {
  model_label: string; // "Endpoint · model" of the last call
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  calls: number;
  unreported_calls: number; // calls whose server sent no usage -- the totals are "at least"
}

/** A task that made no calls this time (unconfigured, or its reading was reused) is null. */
export interface UsageSummary {
  extraction: StageUsage | null;
  filing: StageUsage | null;
  total_tokens: number;
}

export interface RetriedResponse {
  retried: number;
}

/** SPEC 6.4b. The Inbox's four groups. */
export type InboxGroup = "analyse" | "ready" | "done" | "trashed";
export type InboxSort = "newest" | "document_date" | "sender" | "needs_check";
export type CheckFlag =
  | "new_sender"
  | "new_account"
  | "near_match_account"
  | "missing_sender"
  | "missing_title"
  | "missing_date"
  | "unusual_folder"
  | "name_taken";

export interface InboxCard {
  id: string;
  original_filename: string;
  name: string | null;
  folder: string | null;
  sender: string | null;
  document_date: string | null;
  scanned_at: string;
  configuration_name: string | null;
  proposal_status: ProposalStatus;
  later: boolean;
  flags: CheckFlag[];
  analysis_stage: string | null;
  waiting_reason: string | null;
  error: string | null;
  history_entry_id: string | null;
  revertible: boolean;
  processed_at: string | null;
}

export interface InboxQuery {
  group: InboxGroup;
  q?: string;
  sender?: string;
  folder?: string;
  sort?: InboxSort;
  offset?: number;
  limit?: number;
}

export interface InboxPage {
  items: InboxCard[];
  total: number;
  counts: Record<InboxGroup, number>;
  senders: string[];
  folders: string[];
}

export interface AnalyseRequest {
  document_ids: string[];
  configuration_id?: string | null;
}

export interface AnalysisEstimate {
  documents: number;
  readings_needed: number;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  cost: number | null;
  connections: string[];
}

export interface BulkTrashResponse {
  trashed: number;
  failed: { id: string; message: string }[];
}

export interface EndpointStatus {
  id: string;
  name: string;
  reachable: boolean;
}

/** Exactly one of `name_fields` (assembled by the backend) or a hand-written `final_name`. */
export type ApproveRequest = {
  final_folder_path: string;
  document_date: string | null;
} & (
  | {
      name_fields: NameFields;
      // SPEC 6.4: learn the sender / the account with the approval (6.3c).
      remember_sender?: boolean;
      remember_account?: boolean;
    }
  | { final_name: string }
);

export interface HistoryEntry {
  id: string;
  document_id: string;
  original_filename: string;
  final_filename: string;
  final_folder_path: string;
  action: HistoryAction;
  was_overridden: boolean;
  processed_at: string; // ISO 8601 with UTC offset
  revertible: boolean;
}

export interface RoutedResponse {
  document: Document;
  history_entry: HistoryEntry;
}

export interface RevertResponse {
  history_entry: HistoryEntry;
  document: Document;
}

export interface FolderNode {
  path: string; // absolute; the node id
  name: string; // leaf segment
  has_children: boolean;
  children: FolderNode[] | null; // null = not loaded (lazy)
  file_count: number;
  recent_files: string[]; // newest first, capped by the backend
}

export interface CreateFolderRequest {
  parent_path: string;
  name: string;
}

export interface SiblingFile {
  filename: string; // with extension
  created_at: string | null;
  size_bytes: number;
}

export interface FolderContext {
  path: string;
  exists: boolean;
  siblings: SiblingFile[]; // newest first, max 5
  total_file_count: number;
  filename_collision: boolean; // true if the queried filename already exists there
}

export interface Settings {
  allowed_root_folders: string[];
  trash_folder_path: string;
  watch_folder_path: string;
  filename_pattern: string | null;
  filename_pattern_hint: string | null;
  store_ocr_text: boolean; // file a searchable copy of a scan instead of the original
  proposal_batch_size: number; // documents read, then filed, per poll tick; 1-50
}

export type SettingsUpdate = Settings;

export interface AiEndpoint {
  id: string;
  name: string;
  base_url: string;
  api_key_set: boolean; // never the key itself
  timeout_seconds: number; // how long to wait for a reply before the call fails
  context_limit: number | null; // tokens the server holds per request; null = unknown (SPEC 6.3b)
  json_mode: AiJsonMode;
  model_prices: AiModelPrice[]; // a model without an entry shows no cost (SPEC 6.3b)
  used_by: string[]; // names of the configurations using it; it can't be deleted while any do
}

/** How filing asks the endpoint for JSON (SPEC 6.3). */
export type AiJsonMode = "auto" | "json_schema" | "json_object" | "none";

export interface AiEndpointWrite {
  name: string;
  base_url: string;
  api_key?: string; // omitted or empty = leave the stored key unchanged
  timeout_seconds?: number; // omitted = keep the stored value (120 for a new endpoint)
  context_limit: number | null;
  json_mode: AiJsonMode;
  model_prices: AiModelPrice[]; // replaces the stored list whole
}

/** SPEC 6.3c. The parts a name is assembled from, in the order a template may give. */
export type TemplateField = "date" | "sender" | "account" | "title" | "period" | "number";

export interface ArchiveKnowledge {
  name_template: TemplateField[]; // date, sender and title are always in
  filing_rules: string | null;
}

/** Learned from approvals, never entered beforehand (SPEC 6.3c). */
export interface KnownSender {
  id: string;
  short_name: string;
  aliases: string[]; // how it is written in documents
  accounts: KnownAccount[];
}

export interface KnownAccount {
  id: string;
  value: string;
}

export interface KnownSenderWrite {
  short_name: string;
  aliases: string[];
}

/** What one model on a connection costs per million tokens, in the provider's currency. */
export interface AiModelPrice {
  model_name: string;
  price_input_per_million: number | null;
  price_output_per_million: number | null;
}

export type ReadingMethod = "text_layer" | "tesseract" | "vision";
export type FilingStrategy = "one_call" | "two_calls";
/** Sent as reasoning_effort; "default" sends nothing and leaves it to the model. */
export type AiThinking = "default" | "off" | "low" | "medium" | "high";

/** One step of a configuration. A null prompt means the built-in one; a reading that needs
 * no model has no connection or model. */
export interface ConfigurationStep {
  endpoint_id: string | null;
  endpoint_name: string | null; // filled in on the way out, ignored on the way in
  model_name: string | null;
  system_prompt: string | null;
  instructions: string | null;
  temperature: number; // 0-2
  max_tokens: number; // 256-32768
  thinking: AiThinking;
}

export interface ConfigurationWrite {
  name: string;
  reading_method: ReadingMethod;
  reading: ConfigurationStep;
  filing_strategy: FilingStrategy;
  filing: ConfigurationStep;
  folder_system_prompt: string | null; // the second call's prompt, for two_calls
  max_examples: number; // 0-20
  max_folders: number; // 10-1000
  max_text_chars: number; // 1000-30000
}

/** A named way of reading and filing a document (SPEC 6.3a). Exactly one is the default. */
export interface Configuration extends ConfigurationWrite {
  id: string;
  is_default: boolean;
}

/** What a configuration without its own system prompt is sent. */
export interface BuiltinPrompts {
  reading: string;
  filing_one_call: string;
  filing_fields: string;
  filing_folder: string;
}

/** What one call of a configuration is sent, the document left as a placeholder. */
export interface ConfigurationPreviewCall {
  step: string; // "Reading", "Filing", "Fields" or "Folder"
  model_label: string | null; // null when the step needs no model
  system_prompt: string | null;
  user_prompt: string | null;
  note: string;
}

/** Re-analyse, optionally with another configuration from now on. */
export interface RegenerateChoice {
  configuration_id: string;
}

/** One entry in the audit log (SPEC 6.7). `action` is dotted, e.g. "document.approved". */
export interface AuditEvent {
  id: string;
  occurred_at: string; // ISO 8601 with UTC offset
  actor_label: string; // the email, or "system" for the poller
  action: string;
  document_id: string | null;
  summary: string;
  details: Record<string, unknown>;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
}

export type AuditCategory = "documents" | "ai" | "settings" | "auth";

export interface AuditPage {
  items: AuditEvent[];
  next_cursor: string | null;
}

export interface ModelUsage {
  task: string;
  model_label: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  calls: number;
}

export interface AuditUsage {
  days: number;
  analyses: number;
  comparisons: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  by_model: ModelUsage[]; // largest first
}

export interface AuthConfig {
  oidc_enabled: boolean;
  oidc_provider_name: string;
  registration_open: boolean;
  has_users: boolean; // false = nobody has claimed this instance yet
}

export interface AuthUser {
  id: string;
  email: string;
  is_admin: boolean;
}

export interface Credentials {
  email: string;
  password: string;
}

export interface AiModelsRequest {
  base_url: string; // the URL in the form, which may not be saved yet
  api_key?: string; // omitted or empty = test with the saved endpoint's key
  endpoint_id?: string; // set once the endpoint is saved, so the stored key can be used
}

export interface AiModelsResponse {
  models: string[]; // ids from the endpoint's /models, sorted
}

// One day, connection, model and feature, summed (GET /ai/usage).
export type AiUsageFeature = "extraction" | "filing" | "comparison";

export interface AiUsageRow {
  day: string; // YYYY-MM-DD, UTC
  endpoint_id: string | null; // null for usage carried over from an endpoint since renamed or deleted
  connection: string;
  connection_exists: boolean;
  model: string;
  model_configured: boolean;
  feature: AiUsageFeature | string;
  requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  cached_tokens: number;
  without_usage: number; // replied, but sent no token counts
  failed: number; // got no reply
  fallback: number; // from before configurations: made by a later step of a chain
  duration_ms: number; // summed over `timed`
  timed: number;
  cost: number | null; // from the connection's prices; null when no call in the row had one
}

export interface AiUsageReport {
  rows: AiUsageRow[];
  analyses: number;
  comparisons: number;
}

export interface HealthResponse {
  status: string;
  webdav_configured: boolean; // false until this account has connected its Nextcloud
  webdav_reachable: boolean | null; // null when there is nothing to probe
  queue_depth: number;
}

// CLAUDE.md rule 5 applies to the app password as to API keys: it goes in, never comes back.
export interface NextcloudConnection {
  base_url: string;
  username: string;
  password_set: boolean;
}

export interface NextcloudWrite {
  base_url: string;
  username: string;
  password?: string; // omitted or empty = keep the stored one
}

// SPEC 5: non-2xx responses always take this shape.
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
  };
}

/** Name fields as approved or proposed in the Lab; a model may leave any of them out. */
export interface LabNameFields {
  sender: string | null;
  account: string | null;
  title: string | null;
  period: string | null;
  number: string | null;
}

export interface TestSet {
  id: string;
  name: string;
  case_count: number;
  created_at: string;
}

export interface TestCase {
  id: string;
  source: "done" | "archive";
  path: string;
  original_filename: string;
  expected_name: string;
  expected_date: string | null;
  expected_fields: LabNameFields | null;
  expected_folder: string;
}

export interface TestSetDetail extends TestSet {
  cases: TestCase[];
}

export type AddCasesRequest = { document_ids: string[] } | { folder_path: string };

export interface AddedCases {
  added: number;
  skipped: number;
}

export type LabField =
  | "date"
  | "sender"
  | "account"
  | "title"
  | "period"
  | "number"
  | "name"
  | "folder";

export type LabScores = Partial<Record<LabField, number>>;

export interface LabConfigurationSummary {
  configuration_id: string;
  configuration_name: string;
  documents: number;
  finished: number;
  failed: number;
  accuracy: LabScores;
  fully_correct: number;
  average_ms: number | null;
  prompt_tokens: number;
  completion_tokens: number;
  cost: number | null;
}

export interface LabRun {
  id: string;
  test_set_id: string;
  test_set_name: string;
  status: "running" | "done" | "cancelled";
  total: number;
  finished: number;
  waiting_reason: string | null;
  stage: string | null;
  remaining_seconds: number | null;
  created_at: string;
  finished_at: string | null;
  summaries: LabConfigurationSummary[];
}

export interface LabResult {
  id: string;
  test_case_id: string;
  position: number;
  configuration_id: string;
  configuration_name: string;
  original_filename: string;
  expected_name: string;
  expected_date: string | null;
  expected_folder: string;
  expected_fields: LabNameFields | null;
  status: "pending" | "done" | "failed" | "cancelled";
  error: string | null;
  predicted_name: string | null;
  predicted_date: string | null;
  predicted_fields: LabNameFields | null;
  predicted_folder: string | null;
  reading_label: string | null;
  duration_ms: number | null;
  verdicts: LabScores;
  overrides: LabScores;
}

export interface LabRunDetail extends LabRun {
  results: LabResult[];
}

export interface StartRunRequest {
  test_set_id: string;
  configuration_ids: string[];
}
