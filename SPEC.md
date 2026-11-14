# AI Document Router — Specification

## 1. What this is

A self-hosted web application for reviewing AI-generated filenames and destination folders
for scanned documents before they are filed into a WebDAV store (Nextcloud, but nothing
Nextcloud-specific is used). Several people can share one instance; each connects their own
Nextcloud and sees only their own documents, settings and AI configuration (6.5a).

The loop: a scanner drops a PDF into a watch folder → a background job extracts text and
asks an LLM for a filename, a destination folder, and a document date → the user reviews one
document at a time, corrects anything wrong, and approves → the backend renames and moves
the file over WebDAV.

Design goal: reviewing a document should take five seconds and one keystroke when the AI got
it right.

---

## 2. Stack

**Frontend** — Vite, React 18, TypeScript (strict), Tailwind CSS, shadcn/ui, TanStack Query,
react-router-dom, react-hook-form + zod, sonner, react-pdf (pdf.js), lucide-react.
Package manager: pnpm. Lint/format: Biome. Tests: Vitest + Testing Library.

**Backend** — Python 3.12, FastAPI, SQLModel, Alembic, `webdav4`, APScheduler, `pypdf`,
`httpx`, `cryptography` (Fernet). Dependency manager: uv. Lint/format: Ruff. Tests: pytest +
respx.

**Database** — SQLite with WAL enabled. Accessed only through SQLModel so the connection
string is the only thing that changes if this ever needs Postgres.

**Dev environment** — runs natively in WSL2, no containers. Docker Compose exists for
deployment only.

### Non-negotiables

- No Supabase, Firebase, or any BaaS.
- The frontend never contacts the LLM provider or the WebDAV server. It talks only to this
  backend.
- The backend is the only thing holding credentials.

---

## 3. Repository layout

```
ai-document-router/
├── CLAUDE.md
├── SPEC.md
├── IMPLEMENTATION_PLAN.md
├── justfile
├── .env.example
├── backend/
│   ├── pyproject.toml
│   ├── alembic/
│   └── app/
│       ├── main.py            # FastAPI app, CORS, router mounting, lifespan
│       ├── config.py          # pydantic-settings, reads .env
│       ├── log.py             # logging setup, request/tick ids, request log middleware
│       ├── db.py              # engine, session dependency, WAL pragma
│       ├── models.py          # SQLModel tables
│       ├── schemas.py         # Pydantic request/response models
│       ├── deps.py            # DI: session, settings, auth stub
│       ├── api/
│       │   ├── queue.py
│       │   ├── documents.py
│       │   ├── folders.py
│       │   ├── history.py
│       │   ├── audit.py       # the Activity page's events and usage, admin only
│       │   ├── metrics.py     # Prometheus exposition, bearer-token gated
│       │   ├── cursors.py     # (timestamp, id) page cursors, shared by history and audit
│       │   ├── ai.py         # connections and AI usage
│       │   ├── configurations.py # configurations, their previews and built-in prompts
│       │   └── settings.py
│       ├── services/
│       │   ├── webdav.py      # all WebDAV I/O
│       │   ├── extraction.py  # pypdf text + metadata
│       │   ├── ai.py          # LLM call, prompt, response parsing
│       │   ├── connections.py # named endpoints, their capabilities, one step's call
│       │   ├── configurations.py # configurations, default, setups, previews
│       │   ├── analysis.py    # reading and filing per configuration, readings, waiting
│       │   ├── transcribe.py  # pages -> markdown (the vision reading)
│       │   ├── audit.py       # the audit log: record, list, usage totals, retention
│       │   ├── crypto.py      # Fernet encrypt/decrypt for stored secrets
│       │   ├── metrics.py     # every Prometheus metric, and the scrape-time document counts
│       │   └── router.py      # approve / trash / revert orchestration
│       └── jobs/
│           └── poller.py      # APScheduler: scan watch folder, generate proposals
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   └── src/
│       ├── main.tsx, App.tsx, routes.tsx
│       ├── services/api/      # ApiClient interface, HttpApiClient, types.ts
│       ├── hooks/             # TanStack Query hooks, one per resource
│       ├── components/
│       │   ├── ui/            # shadcn primitives
│       │   ├── review/        # DocumentViewer, ReviewForm, SiblingList, ConfidenceBadge
│       │   ├── folders/       # FolderPicker (dialog on desktop, sheet on mobile)
│       │   ├── activity/      # AuditTable, AuditCards, AuditDetails, UsageSummary
│       │   └── layout/        # AppShell, TopBar, MobileActionBar
│       └── pages/             # ReviewPage, HistoryPage, ActivityPage, SettingsPage
└── deploy/
    ├── docker-compose.yml
    ├── backend.Dockerfile
    └── frontend.Dockerfile   # build + nginx serve
```

---

## 4. Data model

### 4.1 Database tables (SQLModel)

**`document`**

| column | type | notes |
|---|---|---|
| `id` | str (uuid4) | PK |
| `user_id` | FK user.id | owner — the account whose Nextcloud it came from |
| `webdav_path` | str | current absolute path; unique per `user_id` |
| `original_filename` | str | as scanned |
| `mime_type` | str | |
| `file_size_bytes` | int | |
| `page_count` | int \| null | null for images |
| `content_hash` | str | sha256 of bytes; dedupe guard for the poller |
| `scanned_at` | datetime | WebDAV last-modified |
| `discovered_at` | datetime | when the poller first saw it |
| `status` | enum | `pending` \| `skipped` \| `moved` \| `trashed` \| `failed` |
| `skip_count` | int | default 0 |
| `proposal_status` | enum | `pending` \| `ready` \| `failed` |
| `proposal_error` | str \| null | |
| `ocr_status` | enum | `not_needed` \| `pending` \| `ready` \| `failed` — see 6.2a |
| `ocr_error` | str \| null | why no searchable copy could be made |
| `configuration_id` | FK ai_configuration.id \| null | the configuration to analyse with; null = the user's default (6.3a) |
| `reading_id` | str \| null | id of the reading the latest analysis filed from; not a foreign key, since `reading` already references `document` |
| `waiting_reason` | str \| null | while `proposal_status=pending`: why the analysis is waiting, e.g. "Read · waiting for Tower to file"; null when it simply has not run yet |
| `analysis_usage` | JSON \| null | tokens the most recent analysis spent, per task — `UsageSummary` (6.3); set on failure too |
| `error_message` | str \| null | set when `status = failed` |

**`proposal`** — one-to-one with document, replaced wholesale on regeneration.

| column | type |
|---|---|
| `id` | str (uuid4) PK |
| `document_id` | FK, unique |
| `suggested_name` | str — **without** extension; assembled from the fields below (7.1a), or a whole name for proposals from before fields existed |
| `target_folder_path` | str |
| `document_date` | date \| null — also the name's `date` field |
| `sender` / `title` | str \| null — null only on a free-name proposal from before fields existed |
| `account` / `period` / `number` | str \| null — optional name fields, null when the document has none |
| `confidence_score` | float 0.0–1.0 |
| `reasoning_text` | str |
| `model_name` | str — which model produced it |
| `created_at` | datetime |
| `prompt_text` | str \| null — the user message(s) sent (8.3.5a) |
| `system_prompt` | str \| null — the system prompt(s) sent; null on proposals from before configurations, which used the built-in one |
| `endpoint_name` | str \| null — the filing connection's name at the time |
| `configuration_id` / `configuration_name` | str \| null — which configuration produced it; not a FK, the name is a copy |
| `reading_label` | str \| null — how the document was read: "Text layer", "Tesseract", or "Connection · model" |

**`history_entry`**

| column | type |
|---|---|
| `id` | str (uuid4) PK |
| `document_id` | FK |
| `original_filename` | str |
| `final_filename` | str — with extension |
| `final_folder_path` | str |
| `source_folder_path` | str — where it came from, needed for revert |
| `action` | enum `moved` \| `trashed` |
| `was_overridden` | bool |
| `suggestion_snapshot` | JSON — the AI's original proposal, for future training |
| `name_fields` | JSON \| null — the approved `{sender, account, title, period, number}`; null for a hand-written name or a trash |
| `processed_at` | datetime |
| `revertible` | bool |

**`app_settings`** — one row per user, created empty the first time it is asked for.

| column | type |
|---|---|
| `user_id` | FK user.id, unique |
| `webdav_base_url` | str — the user's WebDAV root, `""` until connected |
| `webdav_username` | str |
| `webdav_password_encrypted` | bytes \| null — a Nextcloud app password, Fernet with `SECRET_KEY`; never returned |
| `allowed_root_folders` | JSON list[str] |
| `trash_folder_path` | str |
| `watch_folder_path` | str — where the scanner saves; `""` until set, and nothing is ingested before |
| `filename_pattern` | str \| null — regex, soft validation |
| `filename_pattern_hint` | str \| null |
| `store_ocr_text` | bool, default true — file a searchable copy of a scan (6.2a) |
| `proposal_batch_size` | int, default 10 — documents read then filed per poll tick, 1–50 (6.2) |
| `name_template` | JSON list[str], default `["date","sender","account","title","period","number"]` — the order of the name's fields (7.1a); archive knowledge (6.3c) |
| `filing_rules` | str \| null, ≤ 4000 — the owner's rules for naming and filing, sent to every configuration (6.3c) |

**`known_sender`** — a sender short name the owner confirmed (6.3c).

| column | type |
|---|---|
| `id` | str (uuid4) PK |
| `user_id` | FK user.id, indexed |
| `short_name` | str — unique per user, compared case-insensitively |
| `aliases` | JSON list[str] — how the sender is written in documents, e.g. `Zürcher Kantonalbank` |
| `created_at` | datetime |

**`known_account`** — an account the owner holds with a known sender (6.3c).

| column | type |
|---|---|
| `id` | str (uuid4) PK |
| `user_id` | FK user.id, indexed |
| `sender_id` | FK known_sender.id — removed with its sender |
| `value` | str — as it goes into the name, e.g. `738.953-48.5800`; unique per sender after normalising (6.3c) |
| `created_at` | datetime |

**`ai_endpoint`** — one place a model request can be sent. Any number of them (6.3a).

| column | type |
|---|---|
| `id` | str (uuid4) PK |
| `user_id` | FK user.id |
| `name` | str, unique per user — the user's own label ("Workshop PC", "Infomaniak") |
| `base_url` | str |
| `api_key_encrypted` | bytes \| null — Fernet, key from `SECRET_KEY` |
| `timeout_seconds` | int, 10–900, default 120 — how long to wait for a reply |
| `context_limit` | int \| null, 1024–2,000,000 — tokens the server holds per request, prompt and reply together; null = unknown, no check (6.3b) |
| `json_mode` | `auto` (default) \| `json_schema` \| `json_object` \| `none` — how filing asks for JSON (6.3) |
| `model_prices` | JSON list of `{model_name, price_input_per_million, price_output_per_million}` — what a million prompt / reply tokens cost per model on this connection; a model with no entry has no price. Model names are unique within the list; each entry has at least one price |
| `created_at` | datetime |

**`ai_configuration`** — a named recipe for analysing a document: a reading step and a filing
step (6.3a). Any number per user; exactly one is the default once any exists.

| column | type |
|---|---|
| `id` | str (uuid4) PK |
| `user_id` | FK user.id |
| `name` | str, unique per user, ≤ 80 |
| `is_default` | bool — exactly one true per user who has any |
| `reading_method` | `text_layer` \| `tesseract` \| `vision` |
| `reading_endpoint_id` / `reading_model_name` | FK ai_endpoint.id / str, both null unless `vision` |
| `reading_system_prompt` | str \| null, ≤ 20,000 — null = the built-in prompt |
| `reading_instructions` | str \| null, ≤ 2000 — added to the request under the instructions heading |
| `reading_temperature` | float 0–2, default 0.1 |
| `reading_max_tokens` | int 256–32,768, default 4096 — per page batch |
| `reading_thinking` | `default` \| `off` \| `low` \| `medium` \| `high` (6.3b) |
| `filing_endpoint_id` / `filing_model_name` | FK ai_endpoint.id / str — required on save; null only on a configuration migrated from an empty chain |
| `filing_strategy` | `one_call` \| `two_calls` (6.3) |
| `filing_system_prompt` | str \| null — null = built-in; the fields-and-folder prompt, or the fields prompt with `two_calls` |
| `folder_system_prompt` | str \| null — null = built-in; the folder prompt, used only with `two_calls` |
| `filing_instructions` | str \| null, ≤ 2000 |
| `filing_temperature` | float 0–2, default 0.1 |
| `filing_max_tokens` | int 256–32,768, default 2048 |
| `filing_thinking` | as `reading_thinking` |
| `max_examples` | int 0–20, default 8 — example filenames in the filing prompt |
| `max_folders` | int 10–1000, default 250 — folders listed in the filing prompt |
| `max_text_chars` | int 1000–30,000, default 6000 — document text in the filing prompt |
| `created_at` | datetime |

**`reading`** — what one reading setup read off one document, kept for reuse (6.3a).

| column | type |
|---|---|
| `id` | str (uuid4) PK |
| `user_id` | FK user.id |
| `document_id` | FK document.id; unique together with `setup_key` |
| `setup_key` | str — sha256 over everything that changes the result (6.3a) |
| `method` | as `reading_method` |
| `label` | str — "Text layer", "Tesseract", or "Connection · model" |
| `text` | str |
| `created_at` | datetime |

**`test_set`** — a named list of filed documents with a known correct answer (6.4a).

| column | type |
|---|---|
| `id` | str (uuid4) PK |
| `user_id` | FK user.id |
| `name` | str ≤ 80, unique per user |
| `created_at` | datetime |

**`test_case`** — one file in a test set and its correct answer, copied in when it is added,
so a later revert or rename of the source does not change what a run is scored against.

| column | type |
|---|---|
| `id` | str (uuid4) PK |
| `user_id` | FK user.id |
| `test_set_id` | FK test_set.id; unique together with `path` |
| `source` | `done` \| `archive` |
| `document_id` | str \| null — the Done document it came from |
| `path` | str — where the file is now, within `allowed_root_folders` |
| `original_filename` | str — the scanned name for a Done document, the file name otherwise |
| `mime_type` | str |
| `expected_name` | str — the correct stem, no extension |
| `expected_date` | date \| null — from the name's `YYYY.MM.DD` prefix |
| `expected_fields` | JSON \| null — the approved name fields; null for a hand-written or archive name |
| `expected_folder` | str |
| `created_at` | datetime |

**`lab_reading`** — what one reading setup read off one test case, reused by later runs.

| column | type |
|---|---|
| `id` | str (uuid4) PK |
| `user_id` | FK user.id |
| `test_case_id` | FK test_case.id; unique together with `setup_key` |
| `setup_key` / `label` / `text` / `created_at` | as `reading` |

**`lab_run`** — one test set run against one or more configurations.

| column | type |
|---|---|
| `id` | str (uuid4) PK |
| `user_id` | FK user.id |
| `test_set_id` | str — not a foreign key: a run outlives its test set |
| `test_set_name` | str — as it was |
| `configurations` | JSON — `[{ id, name }]` as they were, in the order asked |
| `cancelled` | bool |
| `created_at` | datetime |
| `finished_at` | datetime \| null — set when no result is pending |

**`lab_result`** — one test case filed by one configuration in one run.

| column | type |
|---|---|
| `id` | str (uuid4) PK |
| `user_id` | FK user.id |
| `run_id` | FK lab_run.id |
| `test_case_id` | str |
| `position` | int — the case's place in the set when the run started |
| `configuration_id` / `configuration_name` | str — as they were |
| `original_filename`, `expected_name`, `expected_date`, `expected_fields`, `expected_folder` | copied from the case |
| `status` | `pending` \| `done` \| `failed` \| `cancelled` |
| `waiting_reason` / `error` | str \| null |
| `predicted_name` | str \| null — the assembled stem |
| `predicted_date` / `predicted_fields` / `predicted_folder` | the proposal's |
| `reading_label` | str \| null |
| `duration_ms` | int \| null — reading and filing, as measured |
| `prompt_tokens` / `completion_tokens` | int |
| `cost` | float \| null — null when a call was unpriced |
| `verdicts` | JSON — `{ field: 0 \| 0.5 \| 1 }` as scored (6.4a) |
| `overrides` | JSON — `{ field: 0 \| 0.5 \| 1 }` set by the owner; wins over `verdicts` |
| `finished_at` | datetime \| null |

**`user`** — password and OIDC are two ways into the same row.

| column | type |
|---|---|
| `id` | str (uuid4) PK |
| `email` | str, unique, lowercased |
| `password_hash` | str \| null — scrypt; null for an OIDC-only account |
| `oidc_subject` | str \| null, unique — the provider's `sub` |
| `is_admin` | bool — true for whoever registered first |
| `created_at` | datetime |
| `last_login_at` | datetime \| null |

**`user_session`** — one signed-in browser.

| column | type |
|---|---|
| `id` | str PK — SHA-256 of the cookie value, never the value |
| `user_id` | FK user.id |
| `created_at` / `last_seen_at` / `expires_at` | datetime |

**`oidc_login`** — one in-flight authorization code flow, single-use, expires after 10 minutes.

| column | type |
|---|---|
| `state` | str PK |
| `code_verifier` | str — PKCE; never leaves the server |
| `nonce` | str |
| `redirect_uri` | str |
| `created_at` | datetime |

**`audit_event`** — one thing that happened (6.7). Append-only apart from retention.

| column | type |
|---|---|
| `id` | str (uuid4) PK |
| `occurred_at` | datetime, indexed |
| `user_id` | str \| null, indexed — whose activity it is: the document's owner, else the actor; null for a failed sign-in |
| `actor_user_id` | str \| null — not a FK, so an event outlives its user; null for the poller |
| `actor_label` | str — the email at the time, or `system` |
| `action` | str, indexed — an `AuditAction` value such as `document.approved` |
| `document_id` | str \| null, indexed |
| `summary` | str — one readable sentence |
| `details` | JSON — per action; never a secret |
| `prompt_tokens` / `completion_tokens` / `total_tokens` | int \| null — set when a model was called |

**`ai_call`** — one model call, for the AI usage page (8.12). Written with the audit event of
the analysis or comparison that made it; not subject to audit retention.

| column | type |
|---|---|
| `id` | str (uuid4) PK |
| `user_id` | str, indexed |
| `occurred_at` | datetime, indexed |
| `run_id` | str, indexed — one analysis or comparison |
| `document_id` | str \| null |
| `feature` | `extraction` \| `filing` \| `comparison` |
| `endpoint_id` | str \| null — not a FK, so usage outlives the endpoint |
| `connection` / `model` | str — the endpoint's name and the model, as they were |
| `requests` | int — 1, or a stage's call count for usage carried over from the audit log |
| `prompt_tokens` / `completion_tokens` / `cached_tokens` | int |
| `without_usage` / `failed` / `fallback` | int — of `requests`: no token counts in the reply, no reply, made by a fallback step (always 0 since configurations replaced fallback chains) |
| `duration_ms` / `timed` | int — summed duration over `timed` calls |
| `cost` | float \| null — from reported tokens and the model's prices on that connection at the time of the call; null without a price or without token counts |

### 4.2 Shared TypeScript types

Mirror these exactly in `frontend/src/services/api/types.ts`. The API returns camel-free
snake_case; do not transform keys.

```ts
export type DocumentStatus = "pending" | "skipped" | "moved" | "trashed" | "failed";
export type ProposalStatus = "pending" | "ready" | "failed";

/** The name's parts besides the date (7.1a). Optional ones are null, never "". */
export interface NameFields {
  sender: string;
  account: string | null;
  title: string;
  period: string | null;
  number: string | null;
}

export interface AIProposal {
  suggested_name: string;       // no extension; assembled from document_date + fields
  fields: NameFields | null;    // null: a whole name from before fields existed
  target_folder_path: string;
  document_date: string | null; // "YYYY-MM-DD"
  confidence_score: number;     // 0.0–1.0
  reasoning_text: string;       // plain text, may contain newlines — not markdown
  model_name: string;
  connection_name: string | null;    // the filing connection; null on old proposals
  configuration_name: string | null; // which configuration produced it; null on old proposals
  reading_label: string | null;      // how the document was read
  prompt_text: string | null;
  system_prompt: string;             // as sent, or the built-in one for old proposals
}

/** The reading the latest analysis filed from. */
export interface DocumentReading {
  label: string;                // "Text layer", "Tesseract", "Tower · qwen2.5vl"
  text: string;
}

export interface Document {
  id: string;
  original_filename: string;
  extension: string;            // ".pdf", lowercase, includes the dot
  mime_type: string;
  file_size_bytes: number;
  page_count: number | null;
  scanned_at: string;
  status: DocumentStatus;
  skip_count: number;
  proposal_status: ProposalStatus;
  proposal: AIProposal | null;
  proposal_error: string | null;
  ocr_status: OcrStatus;
  ocr_error: string | null;
  analysis_usage: UsageSummary | null;
  analysis_stage: string | null;    // what the poller is doing right now
  waiting_reason: string | null;    // why a pending analysis waits (6.3a)
  configuration_name: string | null;// a configuration picked for this document; null = default
  reading: DocumentReading | null;
  analysed_at: string | null;       // when the latest analysis finished, successfully or not
}

export interface StageUsage {
  model_label: string;          // "Endpoint · model" of the last call
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  calls: number;
  unreported_calls: number;     // calls whose server sent no usage; totals are a minimum
  cost: number | null;          // sum over the calls whose model had a price; null = none had one
}

export interface UsageSummary {
  extraction: StageUsage | null; // null = the task made no calls this time
  filing: StageUsage | null;
  total_tokens: number;
  cost: number | null;          // null when no stage has a cost, and for usage stored earlier
}

export interface FolderNode {
  path: string;                 // absolute; the node id
  name: string;                 // leaf segment
  has_children: boolean;
  children: FolderNode[] | null;// null = not loaded (lazy)
  file_count: number;
}

export interface SiblingFile {
  filename: string;             // with extension
  created_at: string;
  size_bytes: number;
}

export interface FolderContext {
  path: string;
  exists: boolean;
  siblings: SiblingFile[];      // newest first, max 5
  total_file_count: number;
  filename_collision: boolean;  // true if the queried filename already exists there
}

export interface HistoryEntry {
  id: string;
  document_id: string;
  original_filename: string;
  final_filename: string;
  final_folder_path: string;
  action: "moved" | "trashed";
  was_overridden: boolean;
  processed_at: string;
  revertible: boolean;
}

export type InboxGroup = "analyse" | "ready" | "done" | "trashed";
export type InboxSort = "newest" | "document_date" | "sender" | "needs_check";
export type CheckFlag =
  | "new_sender" | "new_account" | "near_match_account"
  | "missing_sender" | "missing_title" | "missing_date"
  | "unusual_folder" | "name_taken";

export interface InboxCard {
  id: string;
  original_filename: string;
  name: string | null;          // proposed name, or the filed name in Done/Trashed; with extension
  folder: string | null;        // proposed folder, or where it was filed
  sender: string | null;
  document_date: string | null;
  scanned_at: string;
  configuration_name: string | null; // what produced the proposal, else the picked one
  proposal_status: ProposalStatus;
  later: boolean;               // skipped: Ready with a mark
  flags: CheckFlag[];           // Ready only (6.4b)
  analysis_stage: string | null;
  waiting_reason: string | null;
  error: string | null;
  history_entry_id: string | null; // Done/Trashed: the entry revert acts on
  revertible: boolean;
  processed_at: string | null;
}

export interface InboxPage {
  items: InboxCard[];
  total: number;                // in this group after search and filters
  counts: Record<InboxGroup, number>; // unfiltered
  senders: string[];            // filter options in this group
  folders: string[];
  analysing: number;            // To analyse: how many are being worked on right now
}

export interface AnalysisEstimate {
  documents: number;            // open documents among those asked about
  readings_needed: number;      // vision readings not stored yet
  prompt_tokens: number | null; // null: no past calls with these models to go by
  completion_tokens: number | null;
  cost: number | null;          // null: a model without a price, or no tokens to go by
  connections: string[];        // names, so the dialog can say what it waits on
}

export interface Settings {
  allowed_root_folders: string[];
  trash_folder_path: string;
  filename_pattern: string | null;
  filename_pattern_hint: string | null;
  store_ocr_text: boolean;
  proposal_batch_size: number;
}

export type SettingsUpdate = Settings;

/** 6.3c. What is true of the archive whatever the model. */
export type NameFieldKey = "date" | "sender" | "account" | "title" | "period" | "number";

export interface ArchiveKnowledge {
  name_template: NameFieldKey[]; // order of the name's parts; date, sender and title always in
  filing_rules: string | null;
}

export interface KnownSender {
  id: string;
  short_name: string;
  aliases: string[];
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

export interface AiEndpoint {
  id: string;
  name: string;
  base_url: string;
  api_key_set: boolean;         // never the key itself
  timeout_seconds: number;
  context_limit: number | null; // tokens the server holds per request; null = unknown (6.3b)
  json_mode: AiJsonMode;
  model_prices: AiModelPrice[]; // on a write, replaces the stored list whole
  used_by: string[];            // names of the configurations that use it
}

export type AiJsonMode = "auto" | "json_schema" | "json_object" | "none";

export interface AiModelPrice {
  model_name: string;
  price_input_per_million: number | null; // a missing side bills nothing
  price_output_per_million: number | null;
}

export interface AiEndpointWrite {
  name: string;
  base_url: string;
  api_key?: string;             // omitted or empty = leave the stored key unchanged
  timeout_seconds?: number;     // omitted = keep the stored value
  context_limit: number | null;
  json_mode: AiJsonMode;
  model_prices: AiModelPrice[]; // on a write, replaces the stored list whole
}

export type ReadingMethod = "text_layer" | "tesseract" | "vision";
export type FilingStrategy = "one_call" | "two_calls";
export type AiThinking = "default" | "off" | "low" | "medium" | "high";

/** One step's model settings. endpoint_id and model_name are null for a reading that
 * needs no model. A null system_prompt means the built-in one. */
export interface ConfigurationStep {
  endpoint_id: string | null;
  endpoint_name: string | null; // read-only
  model_name: string | null;
  system_prompt: string | null;
  instructions: string | null;
  temperature: number;
  max_tokens: number;
  thinking: AiThinking;
}

export interface AiConfiguration {
  id: string;
  name: string;
  is_default: boolean;
  reading_method: ReadingMethod;
  reading: ConfigurationStep;
  filing_strategy: FilingStrategy;
  filing: ConfigurationStep;
  folder_system_prompt: string | null; // two_calls only; null = built-in
  max_examples: number;
  max_folders: number;
  max_text_chars: number;
}

export type AiConfigurationWrite = Omit<AiConfiguration, "id" | "is_default">;
// endpoint_name is ignored on write.

/** The built-in system prompts a null prompt stands for. */
export interface BuiltinPrompts {
  reading: string;
  filing_one_call: string;
  filing_fields: string;        // two_calls, first call
  filing_folder: string;        // two_calls, second call
}

/** What one call of a saved configuration is sent, the document a placeholder. */
export interface ConfigurationPreview {
  step: string;                 // "Reading", "Filing", "Fields", "Folder"
  model_label: string | null;   // "Connection · model"; null for a reading without a model
  system_prompt: string | null; // null for a reading without a model
  user_prompt: string | null;
  note: string;
}

export interface AuditEvent {
  id: string;
  occurred_at: string;          // ISO 8601 with UTC offset
  actor_label: string;          // the email, or "system" for the poller
  action: string;               // e.g. "document.approved"
  document_id: string | null;
  summary: string;
  details: Record<string, unknown>;  // shape differs per action
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
  by_model: ModelUsage[];       // largest first
}

export interface AuthConfig {
  oidc_enabled: boolean;
  oidc_provider_name: string;
  registration_open: boolean;
  has_users: boolean;           // false = nobody has claimed this instance yet
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
  base_url: string;             // as typed in the form; need not be saved yet
  api_key?: string;             // omitted or empty = test with the saved endpoint's key
  endpoint_id?: string;         // set once saved, so the stored key can be used
}

export interface AiModelsResponse {
  models: string[];             // model ids, sorted
}

// Name fields as approved or proposed in the Lab; a model may leave any of them out.
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

export type LabField = "date" | "sender" | "account" | "title" | "period" | "number" | "name" | "folder";

export interface LabConfigurationSummary {
  configuration_id: string;
  configuration_name: string;
  documents: number;            // results in the run for this configuration
  finished: number;             // done or failed
  failed: number;
  accuracy: Partial<Record<LabField, number>>; // 0–1 per scored field, failures count as 0
  fully_correct: number;        // 0–1 share of finished documents with every field right
  average_ms: number | null;
  prompt_tokens: number;
  completion_tokens: number;
  cost: number | null;          // null when nothing was priced
}

export interface LabRun {
  id: string;
  test_set_id: string;
  test_set_name: string;
  status: "running" | "done" | "cancelled";
  total: number;
  finished: number;
  waiting_reason: string | null; // why pending results are not moving
  stage: string | null;          // what the result being worked on is doing
  remaining_seconds: number | null; // average so far × pending; null before the first result
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
  verdicts: Partial<Record<LabField, number>>;
  overrides: Partial<Record<LabField, number>>;
}

export interface LabRunDetail extends LabRun {
  results: LabResult[];
}
```

---

## 5. API contract

Base path `/api/v1`. JSON in, JSON out.

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/inbox?group=&q=&sender=&folder=&sort=&offset=0&limit=48` | — | `InboxPage` (6.4b) — one group's cards with every group's count |
| GET | `/documents/{id}` | — | `Document` |
| GET | `/documents/{id}/thumbnail` | — | JPEG of the first page, at most 320 px wide, `Cache-Control: private, max-age=86400` |
| POST | `/documents/analyse` | `{ document_ids, configuration_id? }` | `{ queued: number }` — open documents only, others are left alone; same contract as `/regenerate` |
| POST | `/documents/trash` | `{ document_ids }` | `{ trashed: number, failed: [{ id, message }] }` — one at a time, a failure does not stop the rest |
| POST | `/documents/estimate` | `{ document_ids, configuration_id }` | `AnalysisEstimate` (6.4b) |
| GET | `/documents/{id}/content` | — | file bytes, streamed, `Content-Type` per mime |
| POST | `/documents/{id}/approve` | `{ final_folder_path, document_date, name_fields, remember_sender?, remember_account? }` or `{ final_folder_path, document_date, final_name }` — exactly one of the two; with fields the backend assembles the name and `document_date` is required; the remember flags (default false) apply only with fields (6.4) | `{ document, history_entry }` |
| POST | `/documents/{id}/skip` | — | `{ document }` |
| POST | `/documents/{id}/trash` | — | `{ document, history_entry }` |
| POST | `/documents/retry-failed` | — | `{ retried: number }` — every failed proposal of an open document goes back to `pending` |
| POST | `/documents/{id}/regenerate` | `{ configuration_id? }` | `{ document }` — re-runs the analysis; with `configuration_id`, with that configuration from now on (the default's id clears the pick) |
| GET | `/folders/tree?path=/&depth=1` | — | `FolderNode[]` |
| POST | `/folders` | `{ parent_path, name }` | `FolderNode` |
| GET | `/folders/context?path=..&filename=..&all=` | — | `FolderContext` — the 5 newest files; with `all=true` every file, newest first, at most 2000 |
| POST | `/history/{id}/revert` | — | `{ history_entry, document }` |
| GET | `/audit?limit=50&cursor=..&category=..` | — | `AuditPage`, newest first — the caller's own events; `category` is `documents` \| `ai` \| `settings` \| `auth` |
| GET | `/audit/usage?days=30` | — | `AuditUsage` — tokens spent in the window, the caller's own |
| GET | `/settings` | — | `Settings` |
| PUT | `/settings` | `SettingsUpdate` | `Settings` |
| GET | `/settings/nextcloud` | — | `{ base_url, username, password_set }` — never the password |
| PUT | `/settings/nextcloud` | `{ base_url, username, password? }` | same; blank password keeps the stored one |
| POST | `/settings/nextcloud/test` | same | 204, or the reason sign-in failed |
| GET | `/ai/endpoints` | — | `AiEndpoint[]`, by name |
| GET | `/ai/endpoints/status` | — | `[{ id, name, reachable }]`, by name — each probed at most once a minute |
| POST | `/ai/endpoints` | `AiEndpointWrite` | `AiEndpoint` |
| PUT | `/ai/endpoints/{id}` | `AiEndpointWrite` | `AiEndpoint` |
| DELETE | `/ai/endpoints/{id}` | — | 200; 422 while a configuration uses it |
| GET | `/ai/usage?start=YYYY-MM-DD&end=YYYY-MM-DD` | — | `{ rows, analyses, comparisons }` — the caller's calls summed per UTC day, connection, model and feature, both ends inclusive, at most three years |
| GET | `/configurations` | — | `AiConfiguration[]`, default first, then by name |
| POST | `/configurations` | `AiConfigurationWrite` | `AiConfiguration`, 201 — the first one becomes the default |
| PUT | `/configurations/{id}` | `AiConfigurationWrite` | `AiConfiguration` |
| DELETE | `/configurations/{id}` | — | 204; 422 for the default; documents that picked it go back to the default |
| POST | `/configurations/{id}/duplicate` | — | `AiConfiguration`, 201 — named "… (copy)", never the default |
| POST | `/configurations/{id}/default` | — | `AiConfiguration` — makes it the default |
| GET | `/configurations/{id}/preview` | — | `ConfigurationPreview[]` — what each call is sent; walks WebDAV for the folder tree |
| GET | `/configurations/prompts` | — | `BuiltinPrompts` |
| GET | `/lab/test-sets` | — | `TestSet[]`, by name |
| POST | `/lab/test-sets` | `{ name }` | `TestSet`, 201 |
| PUT | `/lab/test-sets/{id}` | `{ name }` | `TestSet` |
| DELETE | `/lab/test-sets/{id}` | — | 204 — its cases go with it; runs stay |
| GET | `/lab/test-sets/{id}` | — | `TestSetDetail` |
| POST | `/lab/test-sets/{id}/cases` | `{ document_ids }` or `{ folder_path }` | `{ added, skipped }` — Done documents, or the files directly in an archive folder (6.4a) |
| DELETE | `/lab/test-cases/{id}` | — | 204 |
| GET | `/lab/runs` | — | `LabRun[]`, newest first |
| POST | `/lab/runs` | `{ test_set_id, configuration_ids }` | `LabRun`, 201 |
| GET | `/lab/runs/{id}` | — | `LabRunDetail` |
| POST | `/lab/runs/{id}/cancel` | — | `LabRun` — pending results become cancelled |
| DELETE | `/lab/runs/{id}` | — | 204 |
| PUT | `/lab/results/{id}/verdict` | `{ field, score }` — `score` 0, 0.5 (folder only), 1, or null to drop the override | `LabResult` |
| GET | `/knowledge` | — | `ArchiveKnowledge` |
| PUT | `/knowledge` | `ArchiveKnowledge` | `ArchiveKnowledge` |
| GET | `/knowledge/senders` | — | `KnownSender[]`, by short name, each with its accounts |
| PUT | `/knowledge/senders/{id}` | `KnownSenderWrite` | `KnownSender` — rename, or change how it is written |
| DELETE | `/knowledge/senders/{id}` | — | 204 — its accounts go with it |
| DELETE | `/knowledge/accounts/{id}` | — | 204 |
| POST | `/ai/models` | `AiModelsRequest` | `{ models: string[] }` — the endpoint's OpenAI-compatible model list |
| GET | `/health` | — | `{ status, webdav_configured, webdav_reachable, queue_depth }` — unauthenticated; with a session cookie, about that user's Nextcloud and queue, otherwise `false, null, 0` |
| GET | `/metrics` | — | Prometheus text format; `Authorization: Bearer <METRICS_TOKEN>`, 404 while unset |
| GET | `/auth/config` | — | `AuthConfig` — public |
| GET | `/auth/me` | — | `AuthUser`, or 401 |
| POST | `/auth/register` | `Credentials` | `AuthUser`, 201, sets the session cookie |
| POST | `/auth/login` | `Credentials` | `AuthUser`, sets the session cookie |
| POST | `/auth/logout` | — | 204, clears the cookie |
| GET | `/auth/oidc/login` | — | 303 to the provider |
| GET | `/auth/oidc/callback` | — | 303 back into the app, or to `/login?error=…` |

**Errors:** non-2xx returns `{ "error": { "code": string, "message": string } }`. `message`
is written for a human and is displayed verbatim in the UI. Codes used:
`not_found`, `validation_error`, `webdav_unreachable`, `webdav_conflict`, `ai_unavailable`,
`outside_allowed_roots`, `filename_collision`, `not_revertible`, `unauthenticated`,
`invalid_credentials`, `admin_required`, `registration_closed`, `oidc_error`, and
`internal_error` (500, for an unhandled exception — the message quotes the request id).

**Request id:** every response carries `X-Request-ID`. A well-formed incoming one
(`[A-Za-z0-9-]`, ≤ 64 characters) is kept, anything else is replaced. It is the id on the
server's log line for that request (6.6).

**`original_suggestion` is not in the approve payload.** The backend already has the
proposal; it snapshots it into `history_entry.suggestion_snapshot` and computes
`was_overridden` itself. This is deliberate — the client cannot lie about what the AI said.

---

## 6. Backend behaviour

### 6.1 WebDAV service (`services/webdav.py`)

The only module that talks to WebDAV. Everything else calls it. Uses `webdav4` with
each user's own connection from `app_settings` — a Nextcloud **app password**, not the
account password. A `Connection(base_url, username, password)` is built per request (or per
poller pass); listings are cached per connection, so two accounts never share a cache entry.

Operations: `list_dir`, `list_dirs_only`, `exists`, `stat`, `read_stream`, `move`, `mkdir`,
`mkdir_p`.

- All paths are absolute and normalized: leading `/`, no trailing slash, no `..` segments.
  A shared `normalize_path()` helper is used at every boundary.
- **Every path derived from user input is validated against `allowed_root_folders`** before
  any operation, in the service layer, not the route handler. The trash folder is the one
  permitted exception. This check is the security boundary of the app — treat it as such.
- Directory listings are cached in-process for 30 seconds, keyed by path, invalidated on any
  write to that path.
- Connection failures raise `WebDAVUnreachable`, mapped to a 503 with `webdav_unreachable`.

### 6.2 Poller (`jobs/poller.py`)

APScheduler interval job inside the API process, every `POLL_INTERVAL_SECONDS` (default 60).
Each tick makes one pass per user with a connected Nextcloud; a pass that fails is rolled
back and logged, and the next user's pass runs regardless.

1. List the watch folder (`watch_folder_path`; skipped while it is empty). For each file not already tracked (by path + `content_hash`),
   create a `document` with `status=pending`, `proposal_status=pending`.
2. For each queued document with `ocr_status=pending`, produce a searchable copy (6.2a).
3. For each document with `proposal_status=pending`, run its configuration's reading and
   filing steps (6.3a), each as soon as the connection it needs is reachable.
4. Skip files still being written: ignore anything whose last-modified is under 10 seconds
   old, and anything with a `.part`/`.tmp`/`.crdownload` extension.
5. Never delete or move anything on the server. The poller only reads. The searchable copy
   it produces is written to the data volume, not to WebDAV — uploading it is approve's job
   (6.4), after the move that puts the document where it belongs.

Jobs are serialized (`max_instances=1`) so a slow LLM call can't stack runs. Each phase is
capped per tick: `POLLER_INGEST_BATCH` (20), `POLLER_OCR_BATCH` (2), and proposals by each
user's `proposal_batch_size` (default 10, 1–50, set in Settings → Analysis). OCR gets the smallest cap because it is the slowest by an order of magnitude.

### 6.2a Searchable copies (`services/searchable.py`)

Only when `store_ocr_text` is on, and only for a PDF whose pages carry no text.

1. `ocrmypdf --output-type pdf --skip-text --optimize 0 -l deu+eng` over the original bytes.
   `--output-type pdf` grafts a text layer onto the original page images rather than
   rewriting the file through ghostscript, so the archived pixels are the scanner's pixels;
   `--optimize 0` keeps pngquant and jbig2enc away from them.
2. The result is cached on the data volume at `OCR_CACHE_DIR`, keyed by `content_hash`.
   Nothing is uploaded here.
3. `ocr_status` becomes `ready`. A document whose proposal had failed on "no text layer" goes
   back to `proposal_status=pending`, so the proposal in the same tick is built from the OCR
   text rather than from the failure.
4. `not_needed` when the document turns out to have a text layer already, or is not a PDF.
   `failed` records why, and that reason is what the proposal reports instead of the generic
   "no text layer".
5. Cached copies older than `OCR_CACHE_MAX_AGE_DAYS` (14) are pruned each tick — the backstop
   for a document that is never filed.

### 6.3 Proposal generation (`services/ai.py`)

Filing is the second step of a configuration (6.3a); reading is the first.

Input assembled by the backend, with the limits of the document's configuration:
- The reading's text, first `max_text_chars` characters (default 6000). If the reading has no
  text at all, set `proposal_status=failed` and `proposal_error` to the OCR failure's own
  reason when there is one, else `"No text layer found in this document."` The document
  stays fully approvable by hand either way.
- The folder tree under the allowed roots, as an indented path list, depth-capped at 4 and
  capped at `max_folders` (default 250), shallower folders kept first.
- Up to `max_examples` (default 8) approved examples (6.3c). Before anything has been
  approved, as many example filenames sampled from across those folders instead, so the model
  can still see the convention; with a `filename_pattern` set, only names that match it
  (without their extension).
- `filename_pattern_hint` if set.
- The known senders and accounts (6.3c), and the owner's filing rules under "Filing rules
  for this archive:".
- The configuration's `filing_instructions` if set, under "Additional instructions from the
  person whose archive this is:", before the document text. In the user message rather than
  the system prompt, so they are part of `prompt_text`. The built-in system prompt says to
  follow them unless they conflict with the reply format.

The system prompt is the configuration's own, or the built-in one when it has none.

**Strategy.** `one_call` asks for the fields and the folder in one reply. `two_calls` asks the
same model twice: first for the fields (the keys below without `target_folder_path`), then,
with the folder list, the fields it just gave and the document text, for `reasoning_text`,
`target_folder_path` and `confidence_score`. The proposal's reasoning joins both replies and
its confidence is the lower of the two. A failure in either call fails the analysis.

Call: `POST {endpoint.base_url}/chat/completions` on the configuration's filing connection,
OpenAI-compatible, with the call parameters in 6.3b. How JSON output is asked for follows the
endpoint's `json_mode`. `auto` asks with `response_format` `json_schema` first, then
`json_object`, then none, each tried only after the previous one was refused with a 400; the
one an endpoint and model accepted is remembered in memory and asked for first from then on.
A fixed mode asks only that way. The schema lists the folders sent in the prompt as an `enum`
for `target_folder_path`, so a schema-constrained model cannot invent one, and forbids other
keys.
Connecting gives up after 5 seconds; the reply gets the endpoint's own `timeout_seconds`
(default 120). One retry on a failed connection, or on a 5xx that arrived within 5 seconds;
a reply that timed out is not retried, since the timeout is already the longest allowed,
and neither is a slower 5xx: that is a proxy such as LiteLLM that has already retried its
upstream and given up.
The model extracts the name's fields; it does not write the name. It must return exactly
these keys, in this order:

```json
{
  "reasoning_text": "Letterhead WIR Bank, account 738.953-48.5800, dated 13.05.2026...",
  "document_date": "2026-05-13",
  "sender": "WIR Bank",
  "account": "738.953-48.5800",
  "title": "Kontoeröffnungsbestätigung",
  "period": null,
  "number": null,
  "target_folder_path": "/Documents/Bank",
  "confidence_score": 0.91
}
```

The system prompt says what each field is (7.1a), that `sender` is the short everyday name
rather than the legal one, and that an optional field is left null rather than filled with
something nearby. The backend assembles `suggested_name` from the fields (7.1a).

Validate the response: `sender` and `title` are non-empty; optional fields that are missing,
empty or a word for "none" become null; no field contains a character 7.1 forbids; the
assembled name passes 7.1; the folder is inside an allowed root; the date parses or is null;
confidence is clamped to 0.0–1.0. On any validation failure, `proposal_status=failed` with
the reason naming the field — never store a half-valid proposal. A null date is valid: the
name is assembled without it, and review asks for one.

The exact user message built for the request (folder tree, sample filenames, naming hint,
document text) is stored on `Proposal.prompt_text` alongside the result, and the system prompt
on `Proposal.system_prompt`, so the review screen can show what was actually sent (8.3.5a).
With `two_calls` both messages are stored, separated by a line naming the second call.
Proposals from before configurations have no stored system prompt; the API returns the
built-in one for them.

**Token usage.** Every reply's OpenAI `usage` block (`prompt_tokens`, `completion_tokens`) is
recorded the moment the reply arrives — before validation, because a rejected reply was paid
for too. A server that sends no usable block still counts as a call, as `unreported_calls`.
One analysis (every reading batch and every filing call) is added
up per task into a `UsageSummary`, stored on `Document.analysis_usage` whether the proposal
succeeded or failed, and logged in one line with the outcome. A reused extraction costs
nothing and shows as `extraction: null`. When the model has prices on that connection, each call's cost is
computed from its reported tokens and stored on `ai_call.cost`; a call without token counts or
without a price has no cost, never a cost of 0.

### 6.3a Configurations and analysis (`services/configurations.py`, `services/analysis.py`)

Reading a document and filing it are two different jobs that may want two different models.
A **configuration** names one way of doing both (4.1 `ai_configuration`):

- **Reading** turns the document into text. `text_layer`: `pypdf` over the searchable copy
  (6.2a) if one was made, else over the file on the server. `tesseract`: pages rendered by
  `pypdfium2`, read by the `tesseract` CLI (`deu+eng`). `vision`: pages rendered and sent to
  a vision model as OpenAI content parts, two pages per request, asked for markdown with the
  step's system prompt and instructions; the parts are stitched with a blank line and only a
  wrapping ```` ```markdown ```` fence is stripped.
- **Filing** reads that text and proposes the fields and folder (6.3).

Each user has at most one **default configuration**, used for every document that has not
been given another. The first configuration a user creates becomes the default; the default
cannot be deleted, only replaced by making another one the default. Changing the default
affects documents still waiting for an analysis; a document that already has a proposal keeps
it. Re-running an analysis with a configuration (`/regenerate` with `configuration_id`) sets
`document.configuration_id`, which every later analysis of that document uses until another
is picked; picking the default clears it. Deleting a configuration returns the documents that
picked it to the default. A connection used by any configuration cannot be deleted.

**Stored readings.** A successful reading is stored per document and **reading setup**: the
method, the connection and model, the effective system prompt, the instructions, the
temperature, output limit and thinking setting, and for `text_layer` whether a searchable copy
existed. Their sha256 is `reading.setup_key`. An analysis whose setup already has a reading
reuses it, so changing only the filing step never reads the pages again. A failed reading is
not stored. `document.reading_id` points at the reading the latest analysis filed from.

**No automatic fallback.** Nothing switches to another connection, model or reading method on
its own. A reading that fails fails the analysis with the reason; the person picks another
configuration if they want one.

**Steps run independently, when their connection is reachable.** Each tick takes the pending
documents of one user (up to `proposal_batch_size`, oldest first) and works in two phases:

1. Read every document whose setup has no stored reading. `text_layer` and `tesseract` need no
   connection and run first; `vision` readings are grouped by connection and model, so each
   model is loaded once per tick on a GPU that holds one at a time.
2. File every document that now has a reading, grouped by filing connection and model.

Before the first call of a group, the connection is probed once per tick (`GET /models`,
5-second connect, 10-second reply; any HTTP answer counts as reachable). An unreachable one
leaves its documents `pending` with a `waiting_reason`: "Waiting for Tower to read", or
"Read · waiting for Tower to file". A call whose connection cannot be reached at all (refused,
no route, DNS) does the same for its document and every later one in that group; the next
tick probes again. Any other failure — a timeout, a refused request, an unusable reply, a
prompt too long — fails the analysis with the reason (8.8 `Try again`). A user with no
configuration waits with "Waiting for a configuration — create one in the Lab".
`waiting_reason` is cleared when the analysis ends either way.

Every proposal records the configuration that produced it (id and name), how the document
was read (`reading_label`), and the filing connection and model.

### 6.3c Archive knowledge (`services/knowledge.py`)

What is true of the owner's archive whatever the model, shared by every configuration: the
name template (7.1a), filing rules, sender short names, accounts and approved examples.
Every row is the user's own (6.5a).

**Normalising.** Two values are the same after trimming, case folding, treating ü/ue, ö/oe,
ä/ae as equal and ignoring everything but letters and digits — for senders, accounts and the
text they are looked for in alike. An account's **key** is the longest run of digits in its
normalised value when that run has at least 5 digits (`frankly 3a CH33 0070 0114 8034 4224 8`
→ `3300700114803442248`), else its whole normalised value.

**Learned, never entered.** Senders and accounts are added only by approving a document with
`remember_sender` / `remember_account` (6.4), never beforehand and never silently. The
Archive knowledge page can rename a sender, edit how it is written, and remove senders and
accounts; it cannot add them.

**What the filing call is given.**
- *Senders:* those whose short name or an alias appears in the normalised text, each as
  `ZKB (written as: Zürcher Kantonalbank)`, under "Known senders — use the short name
  exactly:". When none appears, every short name, up to 100, under "Senders seen before —
  use one of these short names if it is the sender:". Nothing when there are none.
- *Accounts:* only those whose key appears in the normalised text, as `WIR Bank:
  738.953-48.5800`, under "Known accounts in this document — copy the value exactly:".
- *Examples:* approved, not reverted documents filed from fields and whose reading still
  exists, newest first, those whose sender or account appears in the text ahead of the rest,
  up to the configuration's `max_examples`. Each shows the first 400 characters of its text
  with whitespace collapsed, its fields and its folder.
- *Filing rules* as written.

The fields call, the one-call request and the folder call all get the same sections.

### 6.3b Call parameters and context limit (`services/ai.py`)

Every model call sends `temperature` and `max_tokens` from its configuration step (defaults:
filing 0.1 and 2048 tokens, reading 0.1 and 4096 tokens per page batch). The step's
`thinking` setting sends `reasoning_effort`: `off` as `"none"` (Ollama turns thinking off for
models that can), `low`/`medium`/`high` as themselves, `default` not at all. A server that
refuses the parameter fails the call with its own reason.
A reply that stopped at the output limit (`finish_reason: "length"`) is a readable failure,
never parsed as if it were complete: for filing a rejected proposal, for reading a failed
analysis. The reason names the step's output limit to raise; when the message has reasoning
(`reasoning_content` or `reasoning`) but no content, it says the model spent the limit
thinking and offers turning thinking down as well.

OpenAI-compatible servers cannot be told a context size per request, and Ollama silently cuts
a prompt that does not fit, from the start, where the instructions sit. So each endpoint
carries the `context_limit` its server is configured with (for Ollama,
`OLLAMA_CONTEXT_LENGTH` or a Modelfile's `num_ctx`), and before sending, the backend estimates
the prompt: a character count divided by 3 for the system prompt and message, plus a fixed
1600 tokens per page image. The estimate errs high on purpose. When the estimate plus a
minimum reply (filing 512, reading 1024 tokens, or the step's output limit if that is lower)
exceeds the limit, the call is not made and fails with `PromptTooLong`, naming the estimate
and the limit. Otherwise `max_tokens` is lowered to what is left of the limit, if that is less
than the step's output limit.
The context size itself is the connection's, not a configuration's: an OpenAI-compatible
request cannot set it, so it is whatever the server was started with.

### 6.4 Approve, trash, revert (`services/router.py`)

**Approve** — synchronous, the user is waiting.
1. Validate the name (7.1) and that the folder is inside an allowed root.
2. `mkdir_p` the target if it doesn't exist.
3. Re-check for collision immediately before the move; on collision return 409
   `filename_collision`. Do not overwrite, ever.
4. `MOVE` to `{folder}/{name}{extension}`.
5. In one transaction: set `status=moved`, write the `history_entry` with the suggestion
   snapshot and `was_overridden`, and, when asked, remember the sender (`remember_sender`)
   and the account for that sender (`remember_account`, which remembers the sender too).
   Remembering what is already known does nothing.
6. If the move fails, nothing is written and the document stays `pending` — the user's edits
   are still in their browser and they can retry.

**Trash** — same flow, target is `trash_folder_path`, `action=trashed`. On name collision in
trash, suffix with a timestamp rather than failing. Files are never deleted.

**Revert** — `MOVE` the file from `{final_folder_path}/{final_filename}` back to
`source_folder_path/{original_filename}`, set the document back to `status=pending`,
`skip_count=0`, keep its existing proposal, and set `history_entry.revertible=false`. If the
file is no longer where the history says it is, return 409 `not_revertible` and flip
`revertible` to false so the UI stops offering it.

### 6.4b Inbox (`services/inbox.py`)

**Groups.** Open means `pending` or `skipped`.
- `analyse`: open with `proposal_status` `pending` or `failed` (waiting, running, failed).
- `ready`: open with a `ready` proposal. A skipped one is "Later": still here, with a mark.
- `done`: `moved`. `trashed`: `trashed`. Each card carries the latest history entry.

**Search** (`q`, case-insensitive for ASCII as SQLite's `LIKE` is): original filename,
proposed name, filed name, and the text of the document's current reading. **Filters**:
exact sender, exact folder. **Sort**: `newest` (upload, default), `document_date` (newest
first, undated last), `sender` (A–Z, none last), `needs_check` (most flags first, then
newest). Paged by `offset`/`limit` (≤ 100).

**Needs a check** — flags on Ready cards, from the proposal's fields; a free-name proposal
gets only `missing_date`:
- `missing_sender`, `missing_title`, `missing_date`: the field is empty. A reply without a
  sender or title is refused when it arrives (6.3), so in practice only the date is missing.
- `new_sender`: no known sender (6.3c) has that short name or alias, normalised.
- `new_account`: the sender is known and the account's key matches none of its accounts;
  `near_match_account` instead when one is within two edits — likely a misread digit.
- `unusual_folder`: folder patterns come from approved, not reverted documents with fields:
  the folder with every year segment (1900–2099) replaced by `{year}`. Compared against the
  patterns of the same sender and account when that pair has any, else of the sender. A
  sender with no approved documents gets no flag; one occurrence makes a pattern usual.
- `name_taken`: a file of that name is in the proposed folder (cached listing). Only for the
  cards on the page, and silently left out when the Nextcloud cannot be reached; sorting by
  `needs_check` does not count it.

**Thumbnail.** First page rendered to JPEG, cached by content hash in
`THUMBNAIL_CACHE_DIR`, so a file is downloaded and rendered once while the cache lasts. In the
container that is `/tmp/thumbnails`, outside the data volume: never backed up, gone when the
container is recreated, and rendered again on demand. Images are scaled
directly; anything else answers 404 and the card shows an icon.

**Estimate.** For an "Analyse with…" choice: which of the open documents still need a vision
reading for that configuration's reading setup, and the average tokens per document of past
calls with the same feature and model (any model of that feature when the model has none),
times the documents. Cost uses the model's price; unknown when either is missing.

**Connection status.** Every connection probed (6.3a) in parallel, result kept a minute.

### 6.4a Lab: test sets and runs (`services/lab.py`)

The Lab measures configurations against documents whose right answer is known, so changing a
model, a prompt or a parameter is judged on numbers rather than on one document.

**Test cases.** A Done document (status `moved`) contributes its file where it was filed and
its approved name: the fields when it was approved from fields, otherwise the whole name; and
the folder. An archive folder contributes every PDF and image directly in it, up to 100, each
with its current name and folder as the answer, scored as a whole name. The folder must lie
within `allowed_root_folders` (6.1), and every file is read through the WebDAV service only:
the Lab never writes. A path already in the set is skipped. At most 200 cases per set.

**Runs.** Starting a run (at most 6 configurations, a set with at least one case) creates one
`pending` result per case and configuration, copying the case's answer. The poller works
through them after the tick's own analyses, so a run never delays the daily documents and
never uses a connection at the same time as them: each tick works through the oldest
unfinished run's pending results, one configuration after another, for up to one poll
interval (checked before each result), then lets the next tick scan the watch folder. A result whose connection is unreachable
stays pending with a waiting reason, like a document (6.3a). Cancelling turns the pending
results into `cancelled`.

Each result is read and filed exactly as 6.3a and 6.3c describe, with two differences: the
reading is stored per test case and reading setup (`lab_reading`), reusing the Done
document's own reading when one with the same setup exists; and the document being tested,
and any Done document at the same path, is never given as one of its own examples. Its usage
is recorded like any analysis's.

**Scoring.** Values are compared after trimming, case folding, treating ü/ue, ö/oe, ä/ae and
ß/ss as equal and ignoring everything but letters and digits. Both empty is right; a value
where the answer is empty, or none where it has one, is wrong.

- With fields in the answer: date, folder, and each field in the owner's name template.
- With a whole name in the answer: the name, compared whole, and the folder.
- The folder scores 1 when equal, 0.5 when it differs only in year segments or has the same
  parent, else 0.
- A failed result scores 0 on every field.

The owner can overrule any verdict; the override wins and can be dropped again. Per
configuration a run shows each field's average score, the share of finished documents with
every field fully right, the average time per document, tokens and cost.

### 6.5 Authentication (`services/auth.py`, `services/oidc.py`)

`deps.py`'s `get_current_user()` resolves the session cookie to a `user` row and raises 401
otherwise. Every route depends on it except `/health` (the container healthcheck calls it
unauthenticated) and `/auth/*` (which is how someone becomes authenticated).

**Sessions.** A random 256-bit token in an HttpOnly, SameSite=Lax cookie, `Secure` whenever
`APP_BASE_URL` is https. Only the SHA-256 of the token is stored, so a copy of the database
cannot be replayed as a login. Expiry is `SESSION_LIFETIME_DAYS`; logout deletes the row.

**Passwords.** `hashlib.scrypt` (RFC 7914 interactive parameters), per-user salt, stored as
`scrypt$n$r$p$salt$hash`. Minimum 12 characters. A failed login hashes anyway, so an unknown
address and a wrong password take the same time and cannot be told apart.

**First user wins.** Registration is open while the `user` table is empty and the first
account is made admin; afterwards it is closed unless `ALLOW_REGISTRATION=true`. A fresh
deployment is reachable by anyone who knows its URL until someone claims it, so the sign-in
screen doubles as first-run setup to keep that window short.

**OIDC.** One provider, confidential client, Authorization Code flow with PKCE (S256). The
`state`, nonce and verifier live in `oidc_login` (single-use, 10-minute TTL), never in the
browser. Identity comes from `/userinfo`, not from the ID token body: the token arrives over
TLS straight from the token endpoint to a client that authenticated with its secret, which
OIDC Core 3.1.3.7 accepts, and verifying the signature locally would mean JWKS handling and a
JWT dependency this project does not otherwise need. A provider user is matched on `sub`,
then on email — the email fallback links SSO onto an existing password account instead of
forking it. Failures redirect to `/login?error=…` rather than rendering JSON.

### 6.5a Ownership

Every document, history entry, proposal, reading, AI endpoint, configuration, settings row
and audit event belongs to exactly one user, and every service function that reads or changes them
takes that user's id. Another user's id answers **404**, never 403: whether it exists is
none of the caller's business. There is no shared data and no admin override — `is_admin`
only matters for registration.

**Nextcloud connection.** `GET /settings/nextcloud` returns `base_url`, `username` and
`password_set`; `PUT` stores them, where a blank password keeps the stored one;
`POST /settings/nextcloud/test` signs in with the typed values (falling back to the stored
password) and returns 204 or the reason. The URL must be https, or http to a private host
(RFC 1918, loopback, link-local, `100.64.0.0/10`). Until all three are set, anything that
needs WebDAV answers 409 `nextcloud_not_configured`, and the poller skips the user.

### 6.6 Logging (`log.py`)

Stdlib `logging`, configured once in `main.py` at import from `LOG_LEVEL` and `LOG_FORMAT`
(`text` for a terminal, `json` — one object per line — for a collector). uvicorn's own loggers
share the handler; its access log is replaced by one line per request from
`RequestLogMiddleware` (`GET /api/v1/inbox 200 34ms`), with `/health` and `/metrics` at DEBUG.

Every line carries a context: `req=<id>` inside a request (the `X-Request-ID`), `tick=<id>`
inside a poller run, so one request's or one tick's lines can be pulled out together. An
unhandled exception is logged with its traceback under that id and answered with
`internal_error` quoting it.

What is logged: each tick's summary (INFO when it did anything, DEBUG when idle), each AI step's
outcome and duration, waiting documents, sign-ins and failed sign-ins (the address, never the
password), the names of settings that changed, and at DEBUG every WebDAV operation with its
duration. Never logged: API keys, passwords, session tokens, document contents. The frontend
logs failed API calls to the console with the request id; debug lines only in development.

### 6.7 Audit log (`services/audit.py`)

Where the log (6.6) is for finding a bug this week, the audit log answers "who did that, and
what did it cost" months later. `audit.record(session, action, actor=…, summary=…)` adds an
`audit_event` row without committing; the service commits it together with the change it
describes, so there is never an event for a change that rolled back or a change without its
event. A failed sign-in is the one event with no change to ride along with, and commits alone.

Every service that changes something takes a required keyword `actor`: `Actor.from_user(user)`
from a route, `Actor.system()` from the poller.

| Group | Actions |
|---|---|
| Documents | `document.ingested`, `document.ocr_completed`, `document.ocr_failed`, `document.regenerate_requested`, `documents.retry_failed`, `document.approved` (from, to, whether the proposal was overridden), `document.skipped`, `document.trashed`, `history.reverted` |
| AI | `document.analyzed` (outcome, the proposal or the error, the usage breakdown, token columns), `document.compared` (per method usage, token columns) |
| Settings | `settings.updated` (each changed field, old → new), `ai_endpoint.created` / `updated` / `deleted` (name, URL, `api_key_changed: bool` — never the key), `configuration.created` / `updated` / `deleted` / `defaulted` (name; changed fields, old → new) |
| Sign-ins | `auth.registered`, `auth.login` (password or oidc), `auth.login_failed` (the address tried, never the password), `auth.logout` |

`GET /audit` pages on `(occurred_at, id)` like History; `GET /audit/usage` sums the token
columns in SQL and folds the per-model breakdown from the stored usage summaries. Both return
only the signed-in user's events (6.5a). The poller drops events older than `AUDIT_RETENTION_DAYS` (default 365, `0` keeps
everything) once per tick.

### 6.8 Metrics (`services/metrics.py`)

`GET /api/v1/metrics` serves prometheus_client's default registry. It does not exist (404) until
`METRICS_TOKEN` is set, and then needs `Authorization: Bearer <token>`, compared in constant
time — Prometheus cannot hold a session cookie. It is not in the OpenAPI schema.

| Area | Metrics (all prefixed `docrouter_`) |
|---|---|
| HTTP | `http_requests_total{method,route,status}`, `http_request_duration_seconds{method,route}` — `route` is the template under `/api/v1` (`/documents/{document_id}`), or `unmatched` |
| AI | `ai_requests_total{task,step,outcome}` (`ok` \| `rejected` \| `unavailable` \| `unreachable` \| `too_long`), `ai_request_duration_seconds{task,step}`, `ai_tokens_total{task,step,kind}` (`prompt` \| `completion`), `ai_unreported_usage_calls_total{task,step}` — `step` is `Endpoint · model` as Settings names it |
| Pipeline | `analyses_total{outcome}`, `documents_ingested_total`, `documents_routed_total{action}` (`moved` \| `trashed` \| `skipped` \| `reverted`), `ocr_runs_total{outcome}`, `ocr_duration_seconds` |
| Poller | `poller_ticks_total{outcome}` (`ok` \| `failed` \| `skipped`), `poller_tick_duration_seconds`, `poller_last_tick_timestamp_seconds` |
| WebDAV | `webdav_operations_total{operation,outcome}`, `webdav_operation_duration_seconds{operation}`, `webdav_reachable` (from the last operation or `/health` probe) |
| Auth | `auth_logins_total{method,outcome}` |
| Scrape time | `documents{status}` and `queue_proposals{proposal_status}`, counted from the database when scraped |

Counters are bumped after the commit of the thing they count, so a rolled-back approve is not a
routed document. Token counts come from the same recorder that stores usage on the document
(6.3a), so the two cannot disagree. A scrape never touches WebDAV or a model. Series an alert is
likely to divide by are created at zero on startup.

---

## 7. Validation rules

### 7.1 Filename stem (no extension) — enforced in **both** frontend and backend
- Required, trimmed, 1–200 characters.
- Forbidden: `/ \ : * ? " < > |`, control characters, and any `..` sequence.
- No leading or trailing dot, space, or hyphen — trimmed silently on blur, rejected on submit.
- Extension is never editable; it is carried over from the original file.
- If `filename_pattern` is set and does not match: **warning only**, approve stays enabled.
  A one-line amber note under the field; `filename_pattern_hint` is behind "Show the
  pattern", since the hint is often a long naming guide.
- Collision with an existing file in the target: **blocking**, approve disabled.

### 7.1a Name fields

A name is assembled from fields, in the order of the owner's name template (6.3c), joined
by single spaces, empty ones skipped. The default order:

```
date sender [account] title [period] [number]
```

| field | required | example |
|---|---|---|
| `date` | yes | `2026.01.09` — `document_date` written `YYYY.MM.DD` |
| `sender` | yes | `ZKB`, `WIR Bank` — the short name, not the legal name |
| `account` | no | `738.953-48.5800` — IBAN, account, contract or policy the owner holds with the sender |
| `title` | yes | `Kontoeröffnungsbestätigung` — as printed, the document's language, normal case |
| `period` | no | `01.09.2026 - 30.09.2026` |
| `number` | no | `9291491` — invoice or document number |

Each field is trimmed, and a field may not contain a character 7.1 forbids or a control
character. The assembled name must pass 7.1. A template lists each field at most once, always
includes `date`, `sender` and `title`, and may leave the optional ones out. Assembly lives in `services/naming.py` and is mirrored in
`lib/naming.ts` for the live preview; on approve the backend assembles again from the fields
it was sent, so the client's preview is never what gets filed.

A **hand-written name** replaces the fields: approve sends `final_name` instead of
`name_fields`, and the history entry stores no fields.

### 7.2 Folder
- Must be inside one of `allowed_root_folders`. Enforced server-side regardless of the UI.
- New folder names follow the same character rules, 1–100 chars, must not already exist.

### 7.3 Settings
- At least one allowed root; each absolute; no duplicates; none a prefix of another.
- Trash folder absolute, required, **must not be inside any allowed root** — otherwise
  trashed files become valid targets again and can cycle.
- Watch folder absolute, required. It **may** sit inside an allowed root, but must not be one
  or contain one, and must not overlap the trash — filed documents landing directly in it
  would be ingested again. Approve refuses a destination inside it for the same reason.
- Changing the watch folder leaves queued documents where they are: the folders of open
  documents stay permitted for I/O until those documents are filed or trashed.
- `filename_pattern` must compile as a regex; reject on save if it doesn't.
- An endpoint needs a name, unique across endpoints, and a `base_url` that parses as
  `https://` (allow `http://` only for RFC1918 and localhost hosts — the key travels on
  that connection). `context_limit`, when set, is 1024–2,000,000; each model price names a
  model (unique per endpoint), has at least one price, and prices are 0–10,000 per million
  tokens; `json_mode` is one of the four values in 4.1.
- A configuration needs a name (≤ 80, unique per user), a filing connection that exists and
  a non-blank model; a `vision` reading needs the same for reading. Temperatures 0–2, output
  limits 256–32,768, `max_examples` 0–20, `max_folders` 10–1000, `max_text_chars`
  1000–30,000, system prompts ≤ 20,000 characters and not blank (blank means built-in, saved
  as null), instructions ≤ 2000. Every field error names the step.
- Filing rules ≤ 4000 characters, blank saved as null. A sender's short name is 1–80
  characters with no character 7.1 forbids, unique per user ignoring case; up to 10 aliases
  of 1–200 characters each.
- API key is write-only: sent only when non-empty, never returned, never logged, never put
  in the query cache or localStorage.

### 7.4 Confidence display
`>= 0.85` "High" (green) · `0.60–0.84` "Medium" (amber) · `< 0.60` "Low" (red). Below 0.60
also show a one-line banner: "Low confidence — check the folder and date." Confidence never
changes app behaviour. There is no auto-approval at any threshold.

---

## 8. Frontend

### 8.1 Routes
`/` Inbox (`?tab=analyse|ready|done|trashed`) · `/review/:id?` Review · `/history` redirects to `/?tab=done` · `/lab` Lab: runs (`/lab/runs/:id`), `/lab/configurations` (`/new`, `/:id`), `/lab/test-sets` (`/:id`) · `/knowledge` Archive knowledge · `/activity` Activity (`/activity/usage` AI usage) · `/settings` Settings

Every screen sits behind `RequireAuth`, which renders the sign-in screen *in place* rather
than redirecting, so a bookmarked `/settings` survives signing in. `/login` exists only for
the OIDC callback's error redirect. An API error that is not a 401 leaves the app rendered
and lets the outage banner speak — an unreachable backend must not look like a sign-in prompt.

### 8.2 Breakpoints

| | width | Review layout |
|---|---|---|
| Mobile | `< 768px` | single column, form-first, sticky bottom action bar; list in a sheet |
| Tablet | `768–1023px` | single column, larger preview, inline action buttons; list in a sheet |
| Desktop | `1024–1279px` | preview and form in a resizable split, default 55/45; list in a sheet |
| Wide | `>= 1280px` | three panes: list (320 px), then preview and form in the split |

Build mobile-first. Every screen must be usable one-handed on a phone.

### 8.3 Review — desktop

Three panes from 1280 px. Preview and form share a resizable split, min 30% per side, ratio
persisted to localStorage; the list pane is fixed at 320 px.

*List:* the Ready for review group (6.4b) as compact rows — small thumbnail, name (the
original while there is none), folder, "Later" and the check flags — with the Inbox's search,
sender, folder and sort controls stacked above and `Load more` below. The current document's
row is marked (`aria-current`); picking a row makes it current. Below 1280 px the same list
opens in a sheet from a `Ready (n)` button in the Review header, and closes on a pick.

*Preview:* document viewer. `react-pdf` continuous scroll with a toolbar — page indicator,
prev/next, zoom out/in, fit width, rotate. Images use the same toolbar minus paging. Source
filename and size shown above, muted.

*Form:* in this order —
1. Confidence badge (or the analysis error) + "Scanned 3 months ago", absolute time on hover.
2. Document date (date input; required unless the name is hand-written).
3. Name fields (7.1a): Sender and Title required, Account, Period and Number optional — each
   its own text input, on two columns from the `sm` breakpoint. Below them the assembled name
   live, monospace, with the extension as a fixed chip, and a "Write the name by hand"
   checkbox. Ticking it hides the fields and shows the name as an editable input, prefilled
   with the assembled name; unticking returns to the fields as they were. A proposal from
   before fields existed opens with the checkbox ticked. Field errors show under the field;
   7.1 errors on the assembled name show under the preview. The fields are assembled in the
   owner's template order.
3a. **Remember.** With fields, when the sender is not a known short name a checkbox
   "Remember “WIR Bank” as a sender", and when the account is not known for that sender
   "Remember 738.953-48.5800 for WIR Bank". Both start unticked and are sent with the
   approve; nothing is learned without them. Hidden for a hand-written name.
4. Target folder — breadcrumb display + "Choose folder" button.
5. **Produced by** — a bordered block: the configuration's name, how the document was read,
   and the connection · model that filed it, and when the latest analysis finished
   ("Analysed …" or "Failed …", date and time); then `Re-analyse` (with the document's own
   configuration) and `Re-analyse with…` (choose one, 8.8; the dialog shows the chosen configuration's reading and filing step, as does Analyse with… in the Inbox). Inside it, items 5a–5c. Comparing methods is the Lab's
   job (6.4a); the review screen has no comparison.
5a. AI reasoning — muted bordered block, `whitespace-pre-wrap`, clamped to 4 lines with
   "Show more".
5b. **What was sent to the AI** — collapsed by default behind a "Show what was sent to the
    AI" toggle. Expanded, it shows the fixed system instructions and the document-specific
    prompt (folder tree, sample filenames, naming hint, document text) that produced this
    proposal (6.3), each in its own scrollable monospace block. If the document-specific part
    wasn't recorded (a proposal from before this existed), say so instead of showing nothing.
    Lets someone judge a bad proposal and tune Settings instead of guessing.
5c. **Token usage** — one muted line, `Tokens · extraction 3.4K · filing 1.2K · total 4.6K`,
    from `analysis_usage`, shown for failed analyses too. A `<details>` expands it to prompt +
    completion per task with the endpoint · model; `≥` and a note when a call didn't report
    usage. When a model had a price, ` · cost 0.0028` ends the line (and its task's row),
    with the same `≥`. Nothing when no model was reached.
6. **Files already in this folder** — up to 5 siblings, filename (monospace) and date.
   Re-fetches with a 300 ms debounce whenever the target folder changes; it must always show
   the *currently selected* folder, not the AI's original suggestion. This block is the
   reason the app exists.
7. Actions: `Approve & move` (primary) · `Later` (secondary) · `Move to trash`
   (destructive ghost).

### 8.4 Review — mobile

Single column, in this order:
- Compact document card at the top: first page rendered small, filename, page count. Tapping
  it opens a **full-screen viewer sheet** with pinch-zoom and swipe paging, dismissed by
  swipe-down or an X.
- The same form fields, full width, with larger touch targets (min 44 px).
- Sibling files as a compact list rather than a table.
- **Sticky bottom action bar**, safe-area padded: `Approve & move` full width primary, with
  Later and trash as icon buttons beside it. Trash keeps its confirmation dialog.
- Folder picker opens as a **full-screen sheet**, not a modal dialog.
- No keyboard shortcuts and no resizable split on mobile — do not render that machinery.

### 8.5 Folder picker

Lazy tree, one level per `/folders/tree` call. Roots are `allowed_root_folders` — nothing
outside is reachable or selectable. Auto-expands to and highlights the current selection.
Type-to-filter over loaded nodes. `New folder` creates under the selected node. Footer shows
the selected path with Cancel / Select. Desktop: dialog with arrow-key navigation and Enter
to select. Mobile: full-screen sheet, tap to select. Below the tree, the highlighted
folder's newest 20 files and `+ N more`; pressing it loads the rest of that folder, newest
first (`/folders/context?all=true`). Browsing beyond the allowed folders (Settings) only
counts them.

### 8.6 Inbox

The landing page. Header: tabs **To analyse · Ready for review · Done · Trashed**, each with
its count; a select replaces them below 640 px. While another tab is open, a banner says
what is waiting ("3 ready for review · 2 analysing") with a link to that tab.

Controls: search box (debounced 300 ms), sender and folder selects from the group's options,
sort select. A card grid: one column on phones, up to four on wide screens, 48 per page with
`Load more`. Each card: first-page thumbnail (lazy, an icon when there is none), the name
(proposed, filed, or the original while there is none), folder, the configuration and the
upload time; flags as small badges with a readable label; "Later" marked; in To analyse the
stage, waiting reason or error. Clicking a card in To analyse or Ready opens `/review/:id`.
In To analyse, documents being analysed right now come first whatever the sort, with an
"Analysing" badge on the thumbnail and a dashed border, and a line above the grid says
"Analysing n of total now" or "Nothing is being analysed right now" (`InboxPage.analysing`).

**Multi-select** in To analyse and Ready: a checkbox per card and "Select all on page".
The action bar (sticky at the bottom on phones) offers `Analyse` (To analyse) or
`Re-analyse` (Ready), `Analyse with…` and `Trash`. Never approve. `Analyse with…` opens a
dialog: configuration select (default preselected) and the estimate — documents, readings
needed, tokens, cost or "no price", and the connections it uses with their reachability.
Trash confirms with the count. Results come back as a toast; the selection clears.

**Done and Trashed** cards show where the file went and a `Revert` button (confirm naming
the file and its destination); not revertible shows it disabled with "Already reverted, or
the file has moved."

While any To analyse document has failed, that tab offers `Retry failed` (8.8).

Empty states: To analyse "Nothing waiting for analysis." Ready "Nothing to review. New scans
appear here automatically." with `Check for new documents`. Done "Nothing filed yet."
Trashed "Nothing in the trash." With a search or filter: "No documents match." and a
`Clear filters` button.

**Connection status** in the app header: a dot per connection (green reachable, grey not)
with the name beside it from 1024 px, a single summary dot on phones; polled every 60 s.

### 8.7 Settings

One form per section, save disabled until dirty, `Discard changes` alongside, unsaved-changes
navigation guard. Sections: Folders (watch folder, allowed roots list, trash folder), Naming (pattern +
hint, with a live regex validity check), AI endpoints, Analysis (documents per tick), OCR.
Which models read and file, with what prompts, is a configuration (8.13), not a setting.

**AI endpoints.** A list of every configured endpoint — name, URL, a `Key saved` badge, and a
badge per configuration using it — with `Edit` and `Remove` on each and `Add endpoint` below. Not a
single section form: an endpoint is its own resource, so each one saves when *it* is saved.
The API key field renders `••••••••  (saved)` as its placeholder with helper text "Leave blank
to keep the current key" — the key is never returned, so blank can only mean "leave it alone".
Each form has a `Test connection` button that lists the endpoint's `/models` using the URL
currently *typed*, not the one saved, since finding out the URL is wrong is the point; a saved
endpoint's stored key is used when the key field is blank. Below the key and timeout: context
limit (tokens, optional; helper text says to enter what the server is set to, and that for
Ollama that is `OLLAMA_CONTEXT_LENGTH`, 4096 by default on GPUs under 24 GB), JSON mode
(Automatic, JSON schema, JSON object, None), and a list of prices per model: each row a model
name (suggested from the models a connection test listed), input and output price per million
tokens (either may be blank, not both), and a remove button. The list row shows the context
limit and one badge per priced model. Removing asks
for confirmation, and
the backend's refusal ("still used by the configuration …") is surfaced verbatim.

The model field is a dropdown of that endpoint's `/models` ids, not a text field — opening it
is what fetches the list, so no prior step is required, and one list is fetched per endpoint
and shared across the steps of the form. It is disabled until an endpoint is chosen, since there is
nowhere to send the request. Each dropdown offers `Enter a model name manually` to fall back
to free text — for an endpoint that cannot be reached, or one that does not list the model
actually wanted. On a fetch error the reason shows inside the open dropdown; the currently
configured model still appears as an option so a working setting is never blanked by a failed
fetch. An endpoint that answers but lists nothing is not an error.

### 8.8 Review behaviour

- The list is `/inbox?group=ready` with the pane's search, filters and sort; the current
  document is the one in the URL (`/review/:id`), else the list's first row. The URL names
  any document, so one from To analyse opened from the Inbox shows here too, outside the
  list, while it waits.
- Prefetch the next row's document and content in the background.
- Approve → on success: toast naming the destination, the row leaves the list, and the next
  row becomes current — the one below, else the one above. On failure: stay put, keep the
  user's edits, show an inline error above the actions plus a toast. **Never advance on
  failure.**
- Later → the document stays in Ready, marked; the next row becomes current.
- Trash → confirm → the next row.
- Re-analyse and Re-analyse with… → the document goes back to To analyse with its proposal
  replaced when the new one arrives; edits made here are not kept. The next row becomes
  current. Re-analyse with… asks which configuration to use, the one that produced the
  proposal preselected; that choice stays with the document (6.3a).
- With nothing left: "Nothing to review. New scans appear in the Inbox automatically." plus
  a `Back to the Inbox` button.
- A document named in the URL that has been filed or trashed meanwhile says so, with a button
  to the list's first row.
- The list refetches every 60 s and on window refocus, and every 3 s while anything is in To
  analyse. The current document refetches every 3 s while its proposal is pending.
- `proposal_status = "pending"`: form skeleton, actions disabled, and the document's
  `analysis_stage` while the poller is working on it — "Reading the document with Jonas-PC ·
  chandra-ocr-2…", "Choosing a name and folder with Infomaniak · …" — else its
  `waiting_reason` ("Read · waiting for Tower to file"), else "Waiting for the AI proposal."
- `proposal_status = "failed"`: show `proposal_error`, empty but editable fields, a
  `Try again` button hitting `/regenerate`, and full manual approvability.
- **Retry failed** lives on the Inbox's To analyse tab (8.6): a button shown while any
  document there has failed, hitting `/documents/retry-failed`. The poller never revisits a
  failed proposal on its own, so without it a setting corrected afterwards could not heal
  documents that already failed against the old one.

### 8.9 Keyboard shortcuts (desktop only)

`⌘/Ctrl+Enter` approve · `L` later · `F` folder picker · `N` focus the first name field (the name itself when hand-written) · `←`/`→` page.
Ignored while an input is focused, except the ⌘ combo. `?` opens a cheat sheet.

### 8.10 Required states

Every data-backed surface handles loading, empty, and error explicitly. No component ships
with only a happy path.

| Surface | Loading | Empty | Error |
|---|---|---|---|
| Review | full skeleton | "Nothing to review" | error card + Try again |
| Viewer | spinner over pane | — | "Couldn't load the file" + Retry |
| Sibling files | 3 skeleton rows | "This folder is empty" / "will be created" | inline + Retry |
| Folder tree | skeleton nodes | "No subfolders" | inline + Retry |
| History | skeleton rows | "Nothing filed yet" | error card + Try again |
| Activity | skeleton tiles and rows | "Nothing recorded yet" / "No documents were analysed in this period." | error card + Try again |
| Settings | form skeleton | — | error card + Try again |

Mutations disable their trigger and show an in-button spinner. A WebDAV outage surfaces as a
persistent banner in the top bar driven by `/health`, not just per-request errors.

### 8.11 Activity

The signed-in user's own audit log (6.7). Every account has the link.

*Usage.* "AI usage · last 30 days": four stat tiles — Analyses, Comparisons, Tokens,
Prompt · completion — in compact figures (`128K`) with the exact count on hover, two per row
on a phone and four on a wide screen. Below, one line per task and model with its tokens and
calls, largest first. Figures, not a chart: a handful of headline numbers reads faster that
way.

*Events.* A filter row — All, Documents, AI, Settings, Sign-ins — as toggle buttons. Newest
first, `Load more` via cursor. Desktop: a table of when (relative, absolute on hover), who,
the summary, and tokens. Mobile: stacked cards with the summary as the title and who · when ·
tokens beneath. Every event has a `Details` disclosure listing the action, document id, token
split and each `details` entry; nested values show as indented JSON.

### 8.12 AI usage

Reached from the usage summary on Activity. How many tokens the account's model calls used,
modelled on the same page in cash-flow.

- *Period*: This month (default), Last month, This year, or a date range.
- *Tiles*: Requests (with analyses · comparisons), Tokens in (with how many were cached),
  Tokens out, Total (Ø per request that reported tokens, and the period's cost when any call
  had one), Failed (no reply), Without counts (replied without a usage block).
- *Chart*: stacked bars of tokens per day — per month beyond about two months — grouped by
  Connection, Model or Feature (Reading, Filing, Comparisons). The legend toggles a group.
- *Breakdown*: per group, requests, in, out, total, share, Ø / request, Ø duration, failed.
  A connection opens into the models it ran. Pills mark fallback use (from before
  configurations), a model no longer in any configuration, a removed connection. Desktop: a table; phone: one card per line.

### 8.13 Lab — configurations

`/lab` lists the configurations: name, a `Default` badge, the reading ("Text layer",
"Tesseract", or "Tower · qwen2.5vl") and the filing step ("Tower · qwen3:8b · two calls").
Each row has `Edit`, `Duplicate`, `Make default` (not on the default; confirms, saying that
documents waiting for an analysis switch to it and existing proposals stay) and `Delete` (not
on the default; confirms). `New configuration` opens the editor empty. Without any connection
the page says to add one in Settings first. Phone: one card per configuration.

The editor (`/lab/configurations/new`, `/lab/configurations/:id`) is one form with the
unsaved-changes guard, in sections:

- **Name.**
- **Reading.** Method (Text layer, Tesseract, Vision model). For a vision model: connection,
  model (8.7's model dropdown), and the step's prompt and parameters below.
- **Filing.** Connection, model, strategy (One call, Two calls), prompt and parameters.
- **Prompt and parameters**, per model step: the system prompt as a monospace textarea showing
  the built-in text until edited, with `Use the built-in prompt` to go back (saved as null);
  instructions with a `n / 2000` counter; temperature; output limit (tokens); thinking
  (Model default, Off, Low, Medium, High) with helper text that `Off` asks Ollama to skip
  thinking where the model allows it. With two calls, filing shows both the fields prompt and
  the folder prompt.
- **Context.** Examples, folders listed, document text (characters), each with its
  default named in the helper text.
- **What the models are given.** For a saved configuration, collapsed and fetched only once
  opened (it walks WebDAV): each call's system prompt and message with the document as a
  placeholder, from `/configurations/{id}/preview`. It reflects the saved configuration and
  says so.

Saving returns to the list. Validation per 7.3 shows under each field; the backend's message
shows above the actions when it refuses.

### 8.13a Lab — runs and test sets

The Lab has three tabs: **Runs** (`/lab`), **Configurations**, **Test sets**.

*Runs* lists runs newest first: test set, configurations, a progress bar with `n / total`,
the time left, and the waiting reason when nothing moves. `New run` asks for a test set and
one to six configurations, the default preselected. A run's page (`/lab/runs/:id`) shows the
same progress with the current stage and `Cancel`, then a table with a row per
configuration: each scored field's accuracy, fully correct, time per document, tokens and
cost; best value per column in bold. Phone: one card per configuration. Below it, one block
per document: the correct name and folder, and per configuration the proposed name, folder,
and a verdict badge per field — right, partly, wrong, or failed with the error. A badge opens
a menu to mark it right, partly (folder only) or wrong, or to go back to the automatic
verdict; an overruled badge is outlined. The run refetches every 3 s while running.

*Test sets* lists the sets with their case count, `New test set` by name. A set's page
(`/lab/test-sets/:id`) has the name (editable), its cases — original name, correct name and
folder, where it came from — each removable, `Add from Done` (a searchable list of Done
documents with checkboxes) and `Add a folder` (the folder picker; adds the files directly in
it), and `Run this set`.

*Make default* on a configuration confirms with the last finished run containing it next to
the last one containing the current default, field accuracy and fully correct side by side,
or says that neither has been run.

### 8.14 Archive knowledge

`/knowledge`, in the main navigation after Lab. Three cards, each saving on its own:

- **Name template.** The six fields in order, each row with up and down buttons; `Account`,
  `Period` and `Number` have an "Include" checkbox, the others are always in. A live example
  name below, from sample values. Save and Discard; the unsaved-changes guard covers it.
- **Filing rules.** A textarea with a `n / 4000` counter and helper text that every
  configuration is given it.
- **Senders and accounts.** Learned from approvals; says so when empty. One row per sender:
  short name, "written as" aliases, its accounts each with a remove button; `Edit` opens an
  inline form for the short name and the aliases (one per line); `Remove` confirms and says
  the accounts go too.

---

## 9. Design

Clean, utilitarian, data-dense — a tool for repeating one action many times. Light and dark
both fully supported: system preference by default, manual toggle in the top bar, persisted.

- shadcn/ui defaults with Inter; keep the radii and spacing scale rather than re-theming.
- **Monospace for every path, filename, and file listing** — the sibling list, the name
  input, the folder breadcrumb. Spotting a naming inconsistency is the core task and the
  type has to make character-level differences visible.
- One accent colour, used only for the primary action and current selection.
- Red appears only for destructive actions and validation errors.
- Visible focus rings everywhere. `prefers-reduced-motion` respected.

**Copy:** sentence case, active voice, plain verbs. A button and the toast it produces use
the same word — "Approve & move" → "Moved to /Documents/Finance/2026". Errors say what
happened and what to do next, without apologising. Empty states invite an action.

---

## 10. Configuration

`.env` at the repo root, loaded by both sides. See `.env.example`.

Backend: `DATABASE_URL`, `SECRET_KEY`, `POLL_INTERVAL_SECONDS`, `CORS_ORIGINS`, `LOG_LEVEL`,
`LOG_FORMAT` (`text` | `json`), `AUDIT_RETENTION_DAYS` (default 365; `0` keeps every event),
`METRICS_TOKEN` (empty turns `/metrics` off).

Auth: `APP_BASE_URL` (the public URL — decides the OIDC redirect URI and whether the session
cookie is `Secure`), `ALLOW_REGISTRATION`, `SESSION_LIFETIME_DAYS`, and for single sign-on
`OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_PROVIDER_NAME`, `OIDC_SCOPES`.
All three OIDC values must be set for the SSO button to appear; a public client (no secret)
is deliberately unsupported.

Frontend: `VITE_API_BASE_URL`.

The Nextcloud connection and the AI endpoints, models and keys live in the database, per user,
because they are editable in the UI. App passwords and API keys are Fernet-encrypted with
`SECRET_KEY`. Rotating `SECRET_KEY` invalidates them and every user has to re-enter theirs —
document this in the README.

`WEBDAV_BASE_URL`, `WEBDAV_USERNAME`, `WEBDAV_PASSWORD` and `WEBDAV_WATCH_FOLDER` are no longer
read by the app. Migration `c4d8e1f2a9b3` reads them once, if set, to give the existing admin
the connection the instance used to have.

---

## 11. Out of scope

- Roles beyond the single `is_admin` flag, and any user-management UI (accounts are created
  by registering; there is no invite, reset-password, or delete-user flow yet)
- More than one OIDC provider at a time
- Batch approval (bulk analyse and trash are in scope, 8.6)
- Splitting a multi-page PDF into several documents — every page in a file is one document
- Editing or moving files after filing, other than revert
- Notifications, email, scheduling, product analytics (the audit log and operational
  metrics are in scope; tracking how people use the UI is not)
- Multi-tenancy or any second WebDAV account
