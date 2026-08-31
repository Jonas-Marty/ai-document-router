# AI Document Router — Specification

## 1. What this is

A self-hosted, single-user web application for reviewing AI-generated filenames and
destination folders for scanned documents before they are filed into a WebDAV store
(Nextcloud, but nothing Nextcloud-specific is used).

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
│       ├── db.py              # engine, session dependency, WAL pragma
│       ├── models.py          # SQLModel tables
│       ├── schemas.py         # Pydantic request/response models
│       ├── deps.py            # DI: session, settings, auth stub
│       ├── api/
│       │   ├── queue.py
│       │   ├── documents.py
│       │   ├── folders.py
│       │   ├── history.py
│       │   └── settings.py
│       ├── services/
│       │   ├── webdav.py      # all WebDAV I/O
│       │   ├── extraction.py  # pypdf text + metadata
│       │   ├── ai.py          # LLM call, prompt, response parsing
│       │   ├── crypto.py      # Fernet encrypt/decrypt for stored secrets
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
│       │   └── layout/        # AppShell, TopBar, MobileActionBar
│       └── pages/             # ReviewPage, HistoryPage, SettingsPage
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
| `webdav_path` | str | current absolute path; unique |
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
| `error_message` | str \| null | set when `status = failed` |

**`proposal`** — one-to-one with document, replaced wholesale on regeneration.

| column | type |
|---|---|
| `id` | str (uuid4) PK |
| `document_id` | FK, unique |
| `suggested_name` | str — **without** extension |
| `target_folder_path` | str |
| `document_date` | date \| null |
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
| `processed_at` | datetime |
| `revertible` | bool |

**`app_settings`** — single row, `id = 1`.

| column | type |
|---|---|
| `allowed_root_folders` | JSON list[str] |
| `trash_folder_path` | str |
| `filename_pattern` | str \| null — regex, soft validation |
| `filename_pattern_hint` | str \| null |
| `ai_endpoint_url` | str |
| `ai_model_name` | str |
| `vision_model_names` | list[str] — extra models offered on the comparison view only |
| `ai_api_key_encrypted` | bytes \| null — Fernet, key from `SECRET_KEY` |

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

### 4.2 Shared TypeScript types

Mirror these exactly in `frontend/src/services/api/types.ts`. The API returns camel-free
snake_case; do not transform keys.

```ts
export type DocumentStatus = "pending" | "skipped" | "moved" | "trashed" | "failed";
export type ProposalStatus = "pending" | "ready" | "failed";

export interface AIProposal {
  suggested_name: string;       // no extension
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
  ai_endpoint_url: string;
  ai_model_name: string;
  ai_api_key_set: boolean;      // never the key itself
}

export interface SettingsUpdate extends Omit<Settings, "ai_api_key_set"> {
  ai_api_key?: string;          // omitted or empty = leave unchanged
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
  ai_endpoint_url: string;      // as typed in the form; need not be saved yet
  ai_api_key?: string;          // omitted or empty = test with the stored key
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
| POST | `/documents/{id}/approve` | `{ final_name, final_folder_path, document_date }` | `{ document, history_entry }` |
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
| GET | `/settings` | — | `Settings` |
| PUT | `/settings` | `SettingsUpdate` | `Settings` |
| POST | `/settings/ai/models` | `{ ai_endpoint_url, ai_api_key? }` | `{ models: string[] }` — the endpoint's OpenAI-compatible model list |
| GET | `/health` | — | `{ status, webdav_reachable, queue_depth }` |
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
`invalid_credentials`, `admin_required`, `registration_closed`, `oidc_error`.

**`original_suggestion` is not in the approve payload.** The backend already has the
proposal; it snapshots it into `history_entry.suggestion_snapshot` and computes
`was_overridden` itself. This is deliberate — the client cannot lie about what the AI said.

---

## 6. Backend behaviour

### 6.1 WebDAV service (`services/webdav.py`)

The only module that talks to WebDAV. Everything else calls it. Uses `webdav4` with
`WEBDAV_BASE_URL`, `WEBDAV_USERNAME`, `WEBDAV_PASSWORD` from env — a Nextcloud **app
password**, not the account password.

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

1. List the watch folder. For each file not already tracked (by path + `content_hash`),
   create a `document` with `status=pending`, `proposal_status=pending`.
2. For each document with `proposal_status=pending`, generate a proposal (6.3).
3. Skip files still being written: ignore anything whose last-modified is under 10 seconds
   old, and anything with a `.part`/`.tmp`/`.crdownload` extension.
4. Never delete or move anything. The poller only reads.

Jobs are serialized (`max_instances=1`) so a slow LLM call can't stack runs.

### 6.3 Proposal generation (`services/ai.py`)

Input assembled by the backend:
- Extracted text, first 6000 characters (`pypdf`). If the PDF has no text layer, set
  `proposal_status=failed`, `proposal_error="No text layer found — OCR isn't set up yet."`
  The document stays fully approvable by hand.
- The folder tree under the allowed roots, as an indented path list, depth-capped at 4.
- Up to 8 example filenames sampled from across those folders, so the model can see the
  naming convention.
- `filename_pattern_hint` if set.

Call: `POST {ai_endpoint_url}/chat/completions`, OpenAI-compatible, `response_format` JSON,
30-second timeout, one retry on 5xx or timeout. The model must return exactly:

```json
{
  "suggested_name": "2026-08-21_Swisscom_Rechnung",
  "target_folder_path": "/Documents/Finance/2026",
  "document_date": "2026-08-21",
  "confidence_score": 0.91,
  "reasoning_text": "Invoice header shows Swisscom, dated 21.08.2026..."
}
```

Validate the response: name has no extension and no illegal characters, folder is inside an
allowed root, date parses or is null, confidence is clamped to 0.0–1.0. On any validation
failure, `proposal_status=failed` with the reason — never store a half-valid proposal.

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

- **Text layer** — `pypdf`, the model in `ai_model_name`. What ordinary filing does.
- **Tesseract OCR** — pages rendered by `pypdfium2`, read by the `tesseract` CLI (`deu+eng`),
  then the same model. Not ocrmypdf: this wants characters, not a rewritten PDF.
- **Vision** — one result per entry in `vision_model_names`, each sent the rendered pages as
  OpenAI content parts with no transcription, so it is unambiguous which input it used.

Synchronous and on demand — one model call per method. Nothing is stored: the document's
own proposal is untouched, and choosing a result only fills the review form. A method that
cannot run is a result carrying its reason, never an omission.

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

---

## 7. Validation rules

### 7.1 Filename stem (no extension) — enforced in **both** frontend and backend
- Required, trimmed, 1–200 characters.
- Forbidden: `/ \ : * ? " < > |`, control characters, and any `..` sequence.
- No leading or trailing dot, space, or hyphen — trimmed silently on blur, rejected on submit.
- Extension is never editable; it is carried over from the original file.
- If `filename_pattern` is set and does not match: **warning only**, approve stays enabled.
  Amber helper text under the field showing `filename_pattern_hint`.
- Collision with an existing file in the target: **blocking**, approve disabled.

### 7.2 Folder
- Must be inside one of `allowed_root_folders`. Enforced server-side regardless of the UI.
- New folder names follow the same character rules, 1–100 chars, must not already exist.

### 7.3 Settings
- At least one allowed root; each absolute; no duplicates; none a prefix of another.
- Trash folder absolute, required, **must not be inside any allowed root** — otherwise
  trashed files become valid targets again and can cycle.
- `filename_pattern` must compile as a regex; reject on save if it doesn't.
- `ai_endpoint_url` must parse as `https://` (allow `http://` only for RFC1918 hosts).
- API key is write-only: sent only when non-empty, never returned, never logged, never put
  in the query cache or localStorage.

### 7.4 Confidence display
`>= 0.85` "High" (green) · `0.60–0.84` "Medium" (amber) · `< 0.60` "Low" (red). Below 0.60
also show a one-line banner: "Low confidence — check the folder and date." Confidence never
changes app behaviour. There is no auto-approval at any threshold.

---

## 8. Frontend

### 8.1 Routes
`/` Review · `/history` History · `/settings` Settings

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
2. Document date (date input, clearable).
3. File name (text input, monospace, with the extension as a fixed chip on the right).
4. Target folder — breadcrumb display + "Choose folder" button.
5. AI reasoning — muted bordered block, `whitespace-pre-wrap`, clamped to 4 lines with
   "Show more".
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
navigation guard. Sections: Folders (allowed roots list, trash folder), Naming (pattern +
hint, with a live regex validity check), AI (endpoint, model, API key). API key renders as
`••••••••  (saved)` with helper text "Leave blank to keep the current key."

AI has a `Test connection` button that GETs the endpoint's `/models` with the values currently
in the form — the URL being tested is the one typed, not the one saved, since finding out the
URL is wrong is the point. On success the model field becomes a dropdown of the returned ids,
with `Enter a model name manually` to fall back to free text; on failure the reason shows
inline under the button and the field stays a text input. An endpoint that answers but lists
nothing is not an error.

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
- `proposal_status = "pending"`: form skeleton, actions disabled, "Waiting for the AI
  proposal."
- `proposal_status = "failed"`: show `proposal_error`, empty but editable fields, a
  `Try again` button hitting `/regenerate`, and full manual approvability.

### 8.9 Keyboard shortcuts (desktop only)

`⌘/Ctrl+Enter` approve · `S` skip · `F` folder picker · `N` focus name · `←`/`→` page.
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
| Settings | form skeleton | — | error card + Try again |

Mutations disable their trigger and show an in-button spinner. A WebDAV outage surfaces as a
persistent banner in the top bar driven by `/health`, not just per-request errors.

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

Backend: `DATABASE_URL`, `SECRET_KEY`, `WEBDAV_BASE_URL`, `WEBDAV_USERNAME`,
`WEBDAV_PASSWORD`, `WEBDAV_WATCH_FOLDER`, `POLL_INTERVAL_SECONDS`, `CORS_ORIGINS`, `LOG_LEVEL`.

Auth: `APP_BASE_URL` (the public URL — decides the OIDC redirect URI and whether the session
cookie is `Secure`), `ALLOW_REGISTRATION`, `SESSION_LIFETIME_DAYS`, and for single sign-on
`OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_PROVIDER_NAME`, `OIDC_SCOPES`.
All three OIDC values must be set for the SSO button to appear; a public client (no secret)
is deliberately unsupported.

Frontend: `VITE_API_BASE_URL`.

AI endpoint, model, and API key live in the database because they are editable in the UI. The
key is Fernet-encrypted with `SECRET_KEY`. Rotating `SECRET_KEY` invalidates the stored key
and requires re-entering it — document this in the README.

---

## 11. Out of scope

- Roles beyond the single `is_admin` flag, and any user-management UI (accounts are created
  by registering; there is no invite, reset-password, or delete-user flow yet)
- More than one OIDC provider at a time
- Bulk selection or batch approval
- OCR in the *filing* path (a PDF with no text layer still fails its proposal and is handled
  manually; Tesseract exists only as one method on the comparison view)
- Splitting a multi-page PDF into several documents — every page in a file is one document
- Editing or moving files after filing, other than revert
- Notifications, email, scheduling, analytics
- Multi-tenancy or any second WebDAV account
