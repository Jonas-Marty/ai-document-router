// Mirrors backend/app/schemas.py and backend/app/models.py exactly. Keys stay snake_case —
// no case-transform layer (CLAUDE.md).

export type DocumentStatus = "pending" | "skipped" | "moved" | "trashed" | "failed";
export type ProposalStatus = "pending" | "ready" | "failed";
/** `not_needed` covers both "already has a text layer" and "not a PDF": either way there is
 * nothing to add and nothing to wait for. */
export type OcrStatus = "not_needed" | "pending" | "ready" | "failed";
export type HistoryAction = "moved" | "trashed";

export interface AIProposal {
  suggested_name: string; // no extension
  target_folder_path: string;
  document_date: string | null; // "YYYY-MM-DD"
  confidence_score: number; // 0.0-1.0
  reasoning_text: string; // plain text, may contain newlines -- not markdown
  model_name: string;
  // Who answered. connection_name is null for proposals stored before it was recorded.
  connection_name: string | null;
  chosen_by_user: boolean;
  fallback_from: string | null; // labels of earlier steps that didn't answer
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
  extracted_markdown: string | null; // what extraction read off the pages, fed to filing
  extraction_model: string | null;
  extraction_error: string | null; // why extraction failed and the text layer was used
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

export interface QueueResponse {
  items: Document[];
  total_pending: number;
}

export interface RetriedResponse {
  retried: number;
}

export type CompareMethod = "text_layer" | "ocr" | "markdown";

/** One way of reading a document, and what it proposed. `proposal` and `error` are
 * mutually exclusive: a method either produced something or said why it couldn't. */
export interface MethodResult {
  method: CompareMethod;
  model_name: string;
  label: string;
  text_preview: string;
  proposal: AIProposal | null;
  error: string | null;
  duration_ms: number;
  usage: UsageSummary | null;
}

export interface CompareResponse {
  results: MethodResult[];
}

export interface ApproveRequest {
  final_name: string;
  final_folder_path: string;
  document_date: string | null;
}

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

export interface HistoryPage {
  items: HistoryEntry[];
  next_cursor: string | null;
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
  filing_instructions: string | null; // added to the filing prompt; max 2000 characters
  extraction_instructions: string | null; // added to the extraction prompt; max 2000 characters
  proposal_batch_size: number; // documents read, then filed, per poll tick; 1-50
}

export type SettingsUpdate = Settings;

/** The two jobs a model can be given, in the order they run: a vision model reads the pages
 * into markdown, then a text model chooses the filename from what was read. */
export type AiTask = "extraction" | "filing";

export interface AiEndpoint {
  id: string;
  name: string;
  base_url: string;
  api_key_set: boolean; // never the key itself
  timeout_seconds: number; // how long to wait for a reply before trying the next step
  context_limit: number | null; // tokens the server holds per request; null = unknown (SPEC 6.3b)
  json_mode: AiJsonMode;
  price_input_per_million: number | null;
  price_output_per_million: number | null;
  used_by: AiTask[]; // tasks that would lose a step if this were removed
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
  price_input_per_million: number | null;
  price_output_per_million: number | null;
}

export interface AiTaskStep {
  endpoint_id: string;
  endpoint_name: string;
  model_name: string;
}

/** A task's endpoints in the order they are tried: the first that answers wins, and the
 * rest exist for the days it doesn't. */
export interface AiTaskChain {
  task: AiTask;
  steps: AiTaskStep[];
}

/** One connection and model for a single analysis, instead of the configured chain. */
export interface RegenerateChoice {
  endpoint_id: string;
  model_name: string;
}

export interface AiTaskChainUpdate {
  steps: { endpoint_id: string; model_name: string }[];
}

/** What one task is sent right now, built from the current settings with a placeholder in
 * place of the document. */
export interface AiTaskContext {
  task: AiTask;
  chain: string[]; // "Endpoint · model", in the order tried
  system_prompt: string;
  user_prompt: string;
  note: string;
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
  fallback: number; // made by a later step because an earlier one was unavailable
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
