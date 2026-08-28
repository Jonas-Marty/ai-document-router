// Mirrors backend/app/schemas.py and backend/app/models.py exactly. Keys stay snake_case —
// no case-transform layer (CLAUDE.md).

export type DocumentStatus = "pending" | "skipped" | "moved" | "trashed" | "failed";
export type ProposalStatus = "pending" | "ready" | "failed";
export type HistoryAction = "moved" | "trashed";

export interface AIProposal {
  suggested_name: string; // no extension
  target_folder_path: string;
  document_date: string | null; // "YYYY-MM-DD"
  confidence_score: number; // 0.0-1.0
  reasoning_text: string; // plain text, may contain newlines -- not markdown
  model_name: string;
}

export interface Document {
  id: string;
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
}

export interface QueueResponse {
  items: Document[];
  total_pending: number;
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
  filename_pattern: string | null;
  filename_pattern_hint: string | null;
  ai_endpoint_url: string;
  ai_model_name: string;
  ai_api_key_set: boolean; // never the key itself
}

export interface SettingsUpdate extends Omit<Settings, "ai_api_key_set"> {
  ai_api_key?: string; // omitted or empty = leave unchanged
}

export interface AiModelsRequest {
  ai_endpoint_url: string; // the URL in the form, which may not be saved yet
  ai_api_key?: string; // omitted or empty = test with the stored key
}

export interface AiModelsResponse {
  models: string[]; // ids from the endpoint's /models, sorted
}

export interface HealthResponse {
  status: string;
  webdav_reachable: boolean;
  queue_depth: number;
}

// SPEC 5: non-2xx responses always take this shape.
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
  };
}
