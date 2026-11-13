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
│       │   ├── ai.py         # endpoints + task chains
│       │   └── settings.py
│       ├── services/
│       │   ├── webdav.py      # all WebDAV I/O
│       │   ├── extraction.py  # pypdf text + metadata
│       │   ├── ai.py          # LLM call, prompt, response parsing
│       │   ├── ai_tasks.py    # named endpoints, task chains, fallback
│       │   ├── transcribe.py  # pages -> markdown (the extraction task)
│       │   ├── ai_context.py  # what each task is sent, previewed for Settings
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
| `extracted_markdown` | str \| null | what the extraction task read off the pages (6.3a) |
| `extraction_model` | str \| null | which endpoint · model read it |
| `extraction_error` | str \| null | why extraction was skipped or failed; filing falls back to the text layer |
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
| `filing_instructions` | str \| null — the person's own instructions for filing, ≤ 2000 chars (6.3) |
| `extraction_instructions` | str \| null — the same for extraction, ≤ 2000 chars (6.3a) |
| `proposal_batch_size` | int, default 10 — documents read then filed per poll tick, 1–50 (6.2) |

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
| `price_input_per_million` / `price_output_per_million` | float \| null, ≥ 0 — what a million prompt / reply tokens cost on this connection; null = no price |
| `created_at` | datetime |

**`ai_task_step`** — one rung of one task's fallback chain.

| column | type |
|---|---|
| `id` | str (uuid4) PK |
| `user_id` | FK user.id |
| `task` | enum `extraction` \| `filing` |
| `position` | int — 0 is tried first |
| `endpoint_id` | FK ai_endpoint.id |
| `model_name` | str |

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
| `without_usage` / `failed` / `fallback` | int — of `requests`: no token counts in the reply, no reply, made by a step after the first |
| `duration_ms` / `timed` | int — summed duration over `timed` calls |
| `cost` | float \| null — from reported tokens and the connection's prices at the time of the call; null without a price or without token counts |

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
}

export interface StageUsage {
  model_label: string;          // "Endpoint · model" of the last call
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  calls: number;
  unreported_calls: number;     // calls whose server sent no usage; totals are a minimum
}

export interface UsageSummary {
  extraction: StageUsage | null; // null = the task made no calls this time
  filing: StageUsage | null;
  total_tokens: number;
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

export interface Settings {
  allowed_root_folders: string[];
  trash_folder_path: string;
  filename_pattern: string | null;
  filename_pattern_hint: string | null;
  store_ocr_text: boolean;
  filing_instructions: string | null;
  extraction_instructions: string | null;
  proposal_batch_size: number;
}

export type SettingsUpdate = Settings;

export type AiTask = "extraction" | "filing";

export interface AiEndpoint {
  id: string;
  name: string;
  base_url: string;
  api_key_set: boolean;         // never the key itself
  timeout_seconds: number;
  context_limit: number | null; // tokens the server holds per request; null = unknown (6.3b)
  json_mode: AiJsonMode;
  price_input_per_million: number | null;
  price_output_per_million: number | null;
  used_by: AiTask[];            // tasks that would lose a step if this were removed
}

export type AiJsonMode = "auto" | "json_schema" | "json_object" | "none";

export interface AiEndpointWrite {
  name: string;
  base_url: string;
  api_key?: string;             // omitted or empty = leave the stored key unchanged
  timeout_seconds?: number;     // omitted = keep the stored value
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

/** A task's endpoints in the order they are tried: the first that answers wins. */
export interface AiTaskChain {
  task: AiTask;
  steps: AiTaskStep[];
}

export interface AiTaskChainUpdate {
  steps: { endpoint_id: string; model_name: string }[];
}

export interface AiTaskContext {
  task: AiTask;
  chain: string[];        // "Endpoint · model", in the order tried
  system_prompt: string;
  user_prompt: string;    // built from current settings; the document is a placeholder
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
```

---

## 5. API contract

Base path `/api/v1`. JSON in, JSON out.

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/queue?limit=20` | — | `{ items: Document[], total_pending: number }` |
| GET | `/documents/{id}` | — | `Document` |
| GET | `/documents/{id}/content` | — | file bytes, streamed, `Content-Type` per mime |
| POST | `/documents/{id}/approve` | `{ final_folder_path, document_date, name_fields }` or `{ final_folder_path, document_date, final_name }` — exactly one of the two; with fields the backend assembles the name and `document_date` is required | `{ document, history_entry }` |
| POST | `/documents/{id}/skip` | — | `{ document }` |
| POST | `/documents/{id}/trash` | — | `{ document, history_entry }` |
| POST | `/documents/{id}/compare` | — | `{ results: MethodResult[] }` — reads the document every configured way; stores nothing |
| POST | `/documents/retry-failed` | — | `{ retried: number }` — every failed proposal still in the queue goes back to `pending` |
| POST | `/documents/{id}/regenerate` | — | `{ document }` — re-runs the AI proposal |
| GET | `/folders/tree?path=/&depth=1` | — | `FolderNode[]` |
| POST | `/folders` | `{ parent_path, name }` | `FolderNode` |
| GET | `/folders/context?path=..&filename=..` | — | `FolderContext` |
| GET | `/history?limit=50&cursor=..` | — | `{ items: HistoryEntry[], next_cursor }` |
| POST | `/history/{id}/revert` | — | `{ history_entry, document }` |
| GET | `/audit?limit=50&cursor=..&category=..` | — | `AuditPage`, newest first — the caller's own events; `category` is `documents` \| `ai` \| `settings` \| `auth` |
| GET | `/audit/usage?days=30` | — | `AuditUsage` — tokens spent in the window, the caller's own |
| GET | `/settings` | — | `Settings` |
| PUT | `/settings` | `SettingsUpdate` | `Settings` |
| GET | `/settings/nextcloud` | — | `{ base_url, username, password_set }` — never the password |
| PUT | `/settings/nextcloud` | `{ base_url, username, password? }` | same; blank password keeps the stored one |
| POST | `/settings/nextcloud/test` | same | 204, or the reason sign-in failed |
| GET | `/ai/endpoints` | — | `AiEndpoint[]`, by name |
| POST | `/ai/endpoints` | `AiEndpointWrite` | `AiEndpoint` |
| PUT | `/ai/endpoints/{id}` | `AiEndpointWrite` | `AiEndpoint` |
| DELETE | `/ai/endpoints/{id}` | — | 200; 422 while a task still references it |
| GET | `/ai/tasks` | — | `AiTaskChain[]`, one per task |
| GET | `/ai/usage?start=YYYY-MM-DD&end=YYYY-MM-DD` | — | `{ rows, analyses, comparisons }` — the caller's calls summed per UTC day, connection, model and feature, both ends inclusive, at most three years |
| PUT | `/ai/tasks/{task}` | `AiTaskChainUpdate` | `AiTaskChain` — replaces the chain wholesale |
| GET | `/ai/context` | — | `AiTaskContext[]` — what each task is sent right now; walks WebDAV for the folder tree |
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

**Queue ordering:** `pending` first by `scanned_at` ascending, then `skipped` by `skip_count`
ascending, then `scanned_at`. A skipped document never reappears before an unskipped one.

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
3. For each document with `proposal_status=pending`, generate a proposal (6.3).
4. Skip files still being written: ignore anything whose last-modified is under 10 seconds
   old, and anything with a `.part`/`.tmp`/`.crdownload` extension.
5. Never delete or move anything on the server. The poller only reads. The searchable copy
   it produces is written to the data volume, not to WebDAV — uploading it is approve's job
   (6.4), after the move that puts the document where it belongs.

Jobs are serialized (`max_instances=1`) so a slow LLM call can't stack runs. Each phase is
capped per tick: `POLLER_INGEST_BATCH` (20), `POLLER_OCR_BATCH` (2), and proposals by each
user's `proposal_batch_size` (default 10, 1–50, set in Settings → AI instructions). OCR gets the smallest cap because it is the slowest by an order of magnitude.

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

Filing is the second stage of the workflow in 6.3a; extraction is the first.
Each poll runs extraction for its whole batch of pending documents first, then filing for
all of them: on a single home GPU the two models rarely fit together, and alternating per
document would reload both models for each one.

The reply's keys are asked for in the order reasoning_text, document_date,
target_folder_path, suggested_name, confidence_score (system prompt and json_schema alike):
a model writes front to back, so the name is written after the date and folder it is built
from. When the name still fails `filename_pattern` but would match with `document_date` as
its leading date, the backend rewrites that date (DECISIONS.md).

Input assembled by the backend:
- The document's text, first 6000 characters. Preferred source is `extracted_markdown` from
  the extraction task, so the model sees headings, tables and line items rather than one
  flat blob. When extraction is unconfigured or fails, fall back to `pypdf` over the
  searchable copy (6.2a) if one was made, else over the file on the server, and record why
  in `extraction_error`. If there is still no text at all, set `proposal_status=failed` and
  `proposal_error` to the OCR failure's own reason when there is one, else
  `"No text layer found in this document."` The document stays fully approvable by hand
  either way.
- The folder tree under the allowed roots, as an indented path list, depth-capped at 4.
- Up to 8 example filenames sampled from across those folders, so the model can see the
  naming convention. With a `filename_pattern` set, only names that match it (without
  their extension) are sent.
- `filename_pattern_hint` if set.
- `filing_instructions` if set, under "Additional instructions from the person whose archive
  this is:", before the document text. In the user message rather than the system prompt, so
  they are part of `prompt_text` and the review screen shows the instructions that were in
  force for that proposal. The system prompt says to follow them unless they conflict with the
  reply format.

Call: `POST {endpoint.base_url}/chat/completions` for the first step of the filing chain,
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
document text) is stored on `Proposal.prompt_text` alongside the result, so the review screen
can show what was actually sent (8.3.5a). `null` for proposals stored before this field
existed. The system prompt is a fixed constant, not stored per row — the API always returns
its current text.

**Token usage.** Every reply's OpenAI `usage` block (`prompt_tokens`, `completion_tokens`) is
recorded the moment the reply arrives — before validation, because a rejected reply was paid
for too. A server that sends no usable block still counts as a call, as `unreported_calls`.
One analysis (every extraction batch, every fallback step that answered, and filing) is added
up per task into a `UsageSummary`, stored on `Document.analysis_usage` whether the proposal
succeeded or failed, and logged in one line with the outcome. A reused extraction costs
nothing and shows as `extraction: null`. When the connection has prices, each call's cost is
computed from its reported tokens and stored on `ai_call.cost`; a call without token counts or
without a price has no cost, never a cost of 0.

### 6.3a AI workflow (`services/ai_tasks.py`, `services/transcribe.py`)

Reading a document and naming it are two different jobs wanting two different models, so they
are two **tasks**:

- **`extraction`** — pages rendered by `pypdfium2` and sent to a vision model (GOT-OCR 2.0,
  Qwen-VL, and similar) as OpenAI content parts, two pages per request, asked for markdown.
  The parts are stitched with a blank line and stored on `Document.extracted_markdown`. No
  JSON mode and no schema: the reply is prose, and only a wrapping ```` ```markdown ```` fence
  is stripped.
  `extraction_instructions`, if set, follow the fixed request under the same heading filing uses.
- **`filing`** — 6.3, given that markdown.

Extraction runs on **every** document, not only scans without a text layer: a markdown reading
of a table beats a flat one whether or not the PDF happens to carry text. It is never fatal —
an unconfigured, unreachable or failing extraction records its reason in `extraction_error`
and filing proceeds from the text layer.

Each task is configured as an **ordered chain** of `(endpoint, model)` steps. A call walks the
chain and returns the first answer. That is the whole point: the machine at home is first
because it is free and private, and a hosted provider sits behind it so a document still gets
read on the days that machine is off.

**Only `AIUnavailable` moves to the next step** — an endpoint problem is exactly what the next
endpoint is for. A reply that failed validation is the model having *answered*; re-asking a
different one would hide a problem the person needs to see and spend a second call doing it.
An exhausted chain raises with every step's failure named. An empty chain raises
`NoStepsConfigured`, which reads as "no endpoint is assigned to this task yet".
A step that raised `AIUnavailable` is skipped by every chain for the next 5 minutes (keyed
by endpoint and model, in memory), unless every step is cooling down; saving the endpoint in
Settings ends its cooldown. The review screen names the skipped step.

Endpoints are named, and the name is what appears in failure messages and on the review
screen (`"Workshop PC · qwen3"`). Deleting one is **refused** while any task still references
it, rather than quietly shortening that chain. A chain is replaced wholesale on save, not
diffed — it is an ordered list the user edits as a whole.

### 6.3b Call parameters and context limit (`services/ai.py`)

Every model call sends `temperature` and `max_tokens`. Until configurations exist they are
fixed per task: filing 0.1 and 2048 tokens, extraction 0.1 and 4096 tokens per page batch.
A reply that stopped at the output limit (`finish_reason: "length"`) is a readable failure,
never parsed as if it were complete: for filing a rejected proposal, for extraction a failed
reading (filing proceeds from the text layer, as for any failed extraction).

OpenAI-compatible servers cannot be told a context size per request, and Ollama silently cuts
a prompt that does not fit, from the start, where the instructions sit. So each endpoint
carries the `context_limit` its server is configured with (for Ollama,
`OLLAMA_CONTEXT_LENGTH` or a Modelfile's `num_ctx`), and before sending, the backend estimates
the prompt: a character count divided by 3 for the system prompt and message, plus a fixed
1600 tokens per page image. The estimate errs high on purpose. When the estimate plus a
minimum reply (filing 512, extraction 1024 tokens) exceeds the limit, the call is not made
and fails with `PromptTooLong`, naming the estimate and the limit. Otherwise `max_tokens` is
lowered to what is left of the limit, if that is less than the task's output limit.
`PromptTooLong` is an `AIUnavailable`, so the chain moves to the next step, but it puts no step
into cooldown (6.3a): it is about this prompt, not about the endpoint.

### 6.4 Approve, trash, revert (`services/router.py`)

**Approve** — synchronous, the user is waiting.
1. Validate the name (7.1) and that the folder is inside an allowed root.
2. `mkdir_p` the target if it doesn't exist.
3. Re-check for collision immediately before the move; on collision return 409
   `filename_collision`. Do not overwrite, ever.
4. `MOVE` to `{folder}/{name}{extension}`.
5. In one transaction: set `status=moved`, write the `history_entry` with the suggestion
   snapshot and `was_overridden`.
6. If the move fails, nothing is written and the document stays `pending` — the user's edits
   are still in their browser and they can retry.

**Trash** — same flow, target is `trash_folder_path`, `action=trashed`. On name collision in
trash, suffix with a timestamp rather than failing. Files are never deleted.

**Revert** — `MOVE` the file from `{final_folder_path}/{final_filename}` back to
`source_folder_path/{original_filename}`, set the document back to `status=pending`,
`skip_count=0`, keep its existing proposal, and set `history_entry.revertible=false`. If the
file is no longer where the history says it is, return 409 `not_revertible` and flip
`revertible` to false so the UI stops offering it.

### 6.4a Method comparison (`services/compare.py`)

Reading a document is not one thing, and there is no way to know from the outside which way
works best on a given corpus. `POST /documents/{id}/compare` runs every configured method
against one document and reports what each proposed, so a person can judge:

- **Text layer** — `pypdf`, flat.
- **Tesseract OCR** — pages rendered by `pypdfium2`, read by the `tesseract` CLI (`deu+eng`).
  Not ocrmypdf: this wants characters, not a rewritten PDF.
- **Markdown extraction** — the production two-stage path: the extraction chain transcribes
  the pages (6.3a), and that markdown is what gets filed.

All three file through the **same** filing chain, so what is being compared is the *reading*,
with the model that chooses the filename held constant.

Synchronous and on demand — one model call per method. Nothing is stored: the document's
own proposal is untouched, and choosing a result only fills the review form. A method that
cannot run is a result carrying its reason, never an omission. Each result carries its own
`usage` (the markdown method's includes the transcription), so a reading is judged on cost as
well as quality.

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

Every document, history entry, proposal, AI endpoint, task chain, settings row and audit
event belongs to exactly one user, and every service function that reads or changes them
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
`RequestLogMiddleware` (`GET /api/v1/queue 200 34ms`), with `/health` and `/metrics` at DEBUG.

Every line carries a context: `req=<id>` inside a request (the `X-Request-ID`), `tick=<id>`
inside a poller run, so one request's or one tick's lines can be pulled out together. An
unhandled exception is logged with its traceback under that id and answered with
`internal_error` quoting it.

What is logged: each tick's summary (INFO when it did anything, DEBUG when idle), each AI step's
outcome and duration, fallbacks, sign-ins and failed sign-ins (the address, never the
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
| Settings | `settings.updated` (each changed field, old → new), `ai_endpoint.created` / `updated` / `deleted` (name, URL, `api_key_changed: bool` — never the key), `ai_task.updated` (old chain → new) |
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
| AI | `ai_requests_total{task,step,outcome}` (`ok` \| `rejected` \| `unavailable`, one per chain step tried), `ai_request_duration_seconds{task,step}`, `ai_tokens_total{task,step,kind}` (`prompt` \| `completion`), `ai_unreported_usage_calls_total{task,step}`, `ai_fallbacks_total{task}`, `ai_chain_exhausted_total{task}` — `step` is `Endpoint · model` as Settings names it |
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

A name is assembled from fields, in this order, joined by single spaces, empty ones skipped:

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
character. The assembled name must pass 7.1. The template is fixed until Archive knowledge
makes it editable. Assembly lives in `services/naming.py` and is mirrored in
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
  that connection). `context_limit`, when set, is 1024–2,000,000; prices, when set, are
  0–10,000 per million tokens; `json_mode` is one of the four values in 4.1.
- Every step of a task chain needs an endpoint that exists and a non-blank model name.
  An empty chain is valid: it means the task is switched off.
- API key is write-only: sent only when non-empty, never returned, never logged, never put
  in the query cache or localStorage.

### 7.4 Confidence display
`>= 0.85` "High" (green) · `0.60–0.84` "Medium" (amber) · `< 0.60` "Low" (red). Below 0.60
also show a one-line banner: "Low confidence — check the folder and date." Confidence never
changes app behaviour. There is no auto-approval at any threshold.

---

## 8. Frontend

### 8.1 Routes
`/` Review · `/history` History · `/activity` Activity (`/activity/usage` AI usage) · `/settings` Settings

Every screen sits behind `RequireAuth`, which renders the sign-in screen *in place* rather
than redirecting, so a bookmarked `/settings` survives signing in. `/login` exists only for
the OIDC callback's error redirect. An API error that is not a 401 leaves the app rendered
and lets the outage banner speak — an unreachable backend must not look like a sign-in prompt.

### 8.2 Breakpoints

| | width | Review layout |
|---|---|---|
| Mobile | `< 768px` | single column, form-first, sticky bottom action bar |
| Tablet | `768–1023px` | single column, larger preview, inline action buttons |
| Desktop | `>= 1024px` | resizable horizontal split, default 55/45 |

Build mobile-first. Every screen must be usable one-handed on a phone.

### 8.3 Review — desktop

Resizable split, min 30% per side, ratio persisted to localStorage.

*Left:* document viewer. `react-pdf` continuous scroll with a toolbar — page indicator,
prev/next, zoom out/in, fit width, rotate. Images use the same toolbar minus paging. Source
filename and size shown above, muted.

*Right:* review form, in this order —
1. Confidence badge + relative scan time.
2. Document date (date input; required unless the name is hand-written).
3. Name fields (7.1a): Sender and Title required, Account, Period and Number optional — each
   its own text input, on two columns from the `sm` breakpoint. Below them the assembled name
   live, monospace, with the extension as a fixed chip, and a "Write the name by hand"
   checkbox. Ticking it hides the fields and shows the name as an editable input, prefilled
   with the assembled name; unticking returns to the fields as they were. A proposal from
   before fields existed opens with the checkbox ticked. Field errors show under the field;
   7.1 errors on the assembled name show under the preview.
4. Target folder — breadcrumb display + "Choose folder" button.
5. AI reasoning — muted bordered block, `whitespace-pre-wrap`, clamped to 4 lines with
   "Show more".
5a. **What was sent to the AI** — collapsed by default behind a "Show what was sent to the
    AI" toggle. Expanded, it shows the fixed system instructions and the document-specific
    prompt (folder tree, sample filenames, naming hint, document text) that produced this
    proposal (6.3), each in its own scrollable monospace block. If the document-specific part
    wasn't recorded (a proposal from before this existed), say so instead of showing nothing.
    Lets someone judge a bad proposal and tune Settings instead of guessing.
5b. **Token usage** — one muted line, `Tokens · extraction 3.4K · filing 1.2K · total 4.6K`,
    from `analysis_usage`, shown for failed analyses too. A `<details>` expands it to prompt +
    completion per task with the endpoint · model; `≥` and a note when a call didn't report
    usage. Nothing when no model was reached.
6. **Files already in this folder** — up to 5 siblings, filename (monospace) and date.
   Re-fetches with a 300 ms debounce whenever the target folder changes; it must always show
   the *currently selected* folder, not the AI's original suggestion. This block is the
   reason the app exists.
7. Actions: `Approve & move` (primary) · `Skip for now` (secondary) · `Move to trash`
   (destructive ghost).

### 8.4 Review — mobile

Single column, in this order:
- Compact document card at the top: first page rendered small, filename, page count. Tapping
  it opens a **full-screen viewer sheet** with pinch-zoom and swipe paging, dismissed by
  swipe-down or an X.
- The same form fields, full width, with larger touch targets (min 44 px).
- Sibling files as a compact list rather than a table.
- **Sticky bottom action bar**, safe-area padded: `Approve & move` full width primary, with
  skip and trash as icon buttons beside it. Trash keeps its confirmation dialog.
- Folder picker opens as a **full-screen sheet**, not a modal dialog.
- No keyboard shortcuts and no resizable split on mobile — do not render that machinery.

### 8.5 Folder picker

Lazy tree, one level per `/folders/tree` call. Roots are `allowed_root_folders` — nothing
outside is reachable or selectable. Auto-expands to and highlights the current selection.
Type-to-filter over loaded nodes. `New folder` creates under the selected node. Footer shows
the selected path with Cancel / Select. Desktop: dialog with arrow-key navigation and Enter
to select. Mobile: full-screen sheet, tap to select.

### 8.6 History

Newest first, `Load more` via cursor. Desktop: table — processed time (relative, absolute on
hover), original filename, final filename, destination, "edited" marker, Revert button.
Mobile: stacked cards, revert in an overflow menu.

Revert opens a confirmation naming the file and its destination. On success: toast
"Reverted — back in the queue", invalidate history and queue. Non-revertible rows show a
disabled button with tooltip "Already reverted, or the file has moved."

Empty: "Nothing filed yet. Approved and trashed documents show up here."

### 8.7 Settings

One form per section, save disabled until dirty, `Discard changes` alongside, unsaved-changes
navigation guard. Sections: Folders (watch folder, allowed roots list, trash folder), Naming (pattern +
hint, with a live regex validity check), AI endpoints, one card per AI task, AI instructions,
What the models are given, OCR.

**AI instructions.** Two textareas, filing and extraction, each with a `n / 2000` counter that
turns destructive past the limit. Blank saves as `null`. Helper text says they apply to
documents read from now on — regenerate or retry to re-run existing ones.

**What the models are given.** Collapsed by default and fetched only once opened, since it walks
the WebDAV folder tree. Per task: the chain as `Workshop PC · qwen3 → Infomaniak · gpt-4o` (or
"no endpoint assigned yet"), a short note on what travels with the message, then the fixed
instructions and the message in the same monospace blocks as 8.3.5a. A `Refresh` button; it
reflects saved settings, not unsaved edits, and says so. Not a form.

**AI endpoints.** A list of every configured endpoint — name, URL, a `Key saved` badge, and a
badge per task using it — with `Edit` and `Remove` on each and `Add endpoint` below. Not a
single section form: an endpoint is its own resource, so each one saves when *it* is saved.
The API key field renders `••••••••  (saved)` as its placeholder with helper text "Leave blank
to keep the current key" — the key is never returned, so blank can only mean "leave it alone".
Each form has a `Test connection` button that lists the endpoint's `/models` using the URL
currently *typed*, not the one saved, since finding out the URL is wrong is the point; a saved
endpoint's stored key is used when the key field is blank. Below the key and timeout: context
limit (tokens, optional; helper text says to enter what the server is set to, and that for
Ollama that is `OLLAMA_CONTEXT_LENGTH`, 4096 by default on GPUs under 24 GB), JSON mode
(Automatic, JSON schema, JSON object, None), and price per million input and output tokens
(both optional). The list row shows the context limit and the prices when set. Removing asks
for confirmation, and
the backend's refusal ("still assigned to the filing task") is surfaced verbatim.

**AI tasks.** One card per task (6.3a), each an ordered list of steps labelled `First choice`,
`Fallback 1`, `Fallback 2`… with move-up / move-down / remove, and `Add fallback`. Above them,
"Tried top to bottom. The first endpoint that answers is used; the rest are there for when it
can't be reached." An empty chain says what happens instead — extraction falls back to the
PDF's own text layer; filing proposes nothing at all. Each step picks an endpoint from the
saved ones and then a model.

The model field is a dropdown of that endpoint's `/models` ids, not a text field — opening it
is what fetches the list, so no prior step is required, and one list is fetched per endpoint
and shared across both task cards. It is disabled until an endpoint is chosen, since there is
nowhere to send the request. Each dropdown offers `Enter a model name manually` to fall back
to free text — for an endpoint that cannot be reached, or one that does not list the model
actually wanted. On a fetch error the reason shows inside the open dropdown; the currently
configured model still appears as an option so a working setting is never blanked by a failed
fetch. An endpoint that answers but lists nothing is not an error.

### 8.8 Queue behaviour

- Fetch `/queue` on load; current document is the first item.
- Prefetch the next document's content in the background.
- Approve → mutation → on success: toast naming the destination, drop from the cached queue,
  advance, reset the form. On failure: stay put, keep the user's edits, show an inline error
  above the actions plus a toast. **Never advance on failure.**
- Skip → to the back of the queue, advance immediately.
- Trash → confirm → advance.
- Empty queue: "Queue's clear. New scans appear here automatically." plus a
  `Check for new documents` button.
- **Queue overview.** The Review header carries a `Queue` control labelled with the number of
  documents still open, hidden when that is zero. It opens a panel — dialog on desktop,
  full-height sheet on mobile — listing everything queued in the order the screen will reach
  it, each row labelled with the proposed filename (the original only while there is no
  proposal), its destination folder, and whether it is waiting on the AI, failed, or skipped.
  Picking a row makes that document current and closes the panel; the queue is not reordered.
  `/queue` is capped, so a backlog larger than the page says how many are behind the rows.
  When any listed document has failed, the panel offers a bulk `Retry failed`, hitting
  `/documents/retry-failed`. The poller never revisits a failed proposal on its own, so
  without this a setting corrected after the fact cannot heal a queue that already failed
  against the old one.
- Refetch on a 60 s interval and on window refocus.
- `proposal_status = "pending"`: form skeleton, actions disabled, and the document's
  `analysis_stage` while the poller is working on it — "Reading the document with Jonas-PC ·
  chandra-ocr-2…", "Choosing a name and folder with Infomaniak · … (Jonas-PC · … didn't
  answer)" — else "Waiting for the AI proposal." The queue refetches every 3 s while any
  document is pending. Re-running an analysis, or retrying failed ones, starts a poller tick
  at once instead of at the end of the interval.
- `proposal_status = "failed"`: show `proposal_error`, empty but editable fields, a
  `Try again` button hitting `/regenerate`, and full manual approvability.

### 8.9 Keyboard shortcuts (desktop only)

`⌘/Ctrl+Enter` approve · `S` skip · `F` folder picker · `N` focus the first name field (the name itself when hand-written) · `←`/`→` page.
Ignored while an input is focused, except the ⌘ combo. `?` opens a cheat sheet.

### 8.10 Required states

Every data-backed surface handles loading, empty, and error explicitly. No component ships
with only a happy path.

| Surface | Loading | Empty | Error |
|---|---|---|---|
| Queue / review | full skeleton | "Queue's clear" | error card + Try again |
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
  A connection opens into the models it ran. Pills mark fallback use, a model no longer in
  any chain, a removed connection. Desktop: a table; phone: one card per line.

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
- Bulk selection or batch approval
- Splitting a multi-page PDF into several documents — every page in a file is one document
- Editing or moving files after filing, other than revert
- Notifications, email, scheduling, product analytics (the audit log and operational
  metrics are in scope; tracking how people use the UI is not)
- Multi-tenancy or any second WebDAV account
