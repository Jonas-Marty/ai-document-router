# Implementation Plan

Build in this order. Each milestone ends with `just check` passing and the stated acceptance
criteria demonstrably working. Do not start a milestone before the previous one is verified.

---

## M1 — Skeleton and tooling

Scaffold both sides so the dev loop works before any feature exists.

- `backend/` with uv, FastAPI, `/api/v1/health` returning `{status: "ok"}`, Ruff, pytest, mypy.
- `frontend/` with pnpm, Vite, React, TS strict, Tailwind, shadcn/ui initialised, Biome,
  Vitest. Vite dev proxy for `/api` → `http://localhost:8000`.
- `justfile` with every command in CLAUDE.md. `just dev` runs both concurrently.
- `.env.example`, `.gitignore`, `README.md` with WSL2 setup notes.

**Done when:** `just dev` starts both, the frontend renders a page that fetches `/health`
successfully, and a code change to either side is visible in under 5 seconds.

---

## M2 — Data layer

- SQLModel tables per SPEC §4.1. Alembic initialised, first migration applied.
- SQLite with WAL and `foreign_keys=ON` set via a connection pragma.
- Fernet helpers in `services/crypto.py`.
- Settings table seeded with one row on first startup, defaults from env where sensible.
- `GET`/`PUT /settings` working end to end, with the API key write-only and the validation
  rules in SPEC §7.3.

**Done when:** settings round-trip through the API, the key never appears in a response, and
`just upgrade` on an empty database produces a working schema.

---

## M3 — WebDAV service

- `services/webdav.py` with the full operation set in SPEC §6.1.
- `normalize_path()` and `assert_within_allowed_roots()`, used at every boundary.
- 30-second directory listing cache with write invalidation.
- `WebDAVUnreachable` mapped to 503 in the global handler.
- `/health` extended to report `webdav_reachable` and `queue_depth`.

**Done when:** tests cover path escapes (`..`, absolute overrides, unicode tricks), out-of-root
rejection, and cache invalidation — all against a mocked client, no real server needed. Then
verify manually against your actual Nextcloud once.

**This is the highest-risk milestone. Take it slowly and test it properly.**

---

## M4 — Ingestion and proposals

- `services/extraction.py` — pypdf text extraction, page count, mime detection, sha256.
- `services/ai.py` — prompt assembly per SPEC §6.3, OpenAI-compatible call, strict response
  validation, retry on 5xx/timeout.
- `jobs/poller.py` — APScheduler interval job, `max_instances=1`, partial-write guards.
- `GET /queue`, `GET /documents/{id}`, `GET /documents/{id}/content` (streamed),
  `POST /documents/{id}/regenerate`.

**Done when:** dropping a real PDF into the watch folder produces a document with a valid
proposal within one poll interval, and a PDF with no text layer produces
`proposal_status=failed` with a readable message rather than an exception.

---

## M5 — Routing actions

- `services/router.py` — approve, trash, revert per SPEC §6.4, each transactional.
- `POST /approve`, `/skip`, `/trash`, `GET /history`, `POST /history/{id}/revert`.
- `GET /folders/tree`, `POST /folders`, `GET /folders/context`.

**Done when:** a document can be approved, appears in history, is physically in the right
place on the WebDAV server with the right name, and reverting puts it back in the watch
folder and the queue. Collision returns 409 without touching the file.

**At this point the backend is complete.** Everything after this is UI.

---

## M6 — Frontend foundation

- `services/api/` — `types.ts` mirroring SPEC §4.2, `ApiClient` interface, `HttpApiClient`.
- TanStack Query hooks, one per resource, with sensible cache keys and invalidation.
- `AppShell` — top bar, nav, dark mode toggle (system default, manual override, persisted),
  WebDAV outage banner driven by `/health`.
- Routing for the three pages, each a stub.
- Shared loading, empty, and error components used everywhere from here on.

**Done when:** all three routes render, dark mode works, and stopping the backend produces the
outage banner rather than a broken page.

---

## M7 — Review screen, mobile first

Build the mobile layout completely, then add the desktop split. Not the other way round.

- Mobile: compact document card, full-screen viewer sheet, form fields, compact sibling list,
  sticky bottom action bar with safe-area padding.
- Desktop: resizable split with persisted ratio, full PDF toolbar, keyboard shortcuts and
  cheat sheet.
- Form: all fields per SPEC §8.3, validation per §7.1, debounced folder-context fetch driving
  the sibling list and collision check, "edited" chips on changed fields.
- Queue behaviour per §8.8, including the never-advance-on-failure rule.

**Done when:** the full review loop works one-handed on a 375 px viewport and with keyboard
only at 1440 px, and every state in §8.10 can be triggered and looks right.

---

## M8 — Folder picker

Lazy tree rooted at the allowed folders, auto-expand to current selection, type-to-filter,
create-new-folder. Dialog on desktop with arrow-key navigation, full-screen sheet on mobile.

**Done when:** no path outside the allowed roots is reachable through the UI, and a folder
created in the picker is immediately selectable.

---

## M9 — History and settings screens

History table on desktop, cards on mobile, cursor pagination, revert with confirmation.
Settings form per SPEC §8.7 with dirty tracking and a navigation guard.

**Done when:** an approved document can be found in history and reverted from a phone.

---

## M10 — Deployment

- `deploy/backend.Dockerfile` (uv, non-root user), `deploy/frontend.Dockerfile` (build +
  nginx serving the static bundle and proxying `/api`).
- `docker-compose.yml` with a named volume for the SQLite file and `.env` wiring.
- README: WSL2 dev setup, first-run steps, `SECRET_KEY` rotation warning, backup guidance
  (the SQLite file plus nothing else — documents live on WebDAV).

**Done when:** `just up` produces a working application from a clean checkout, and the
database survives a container recreate.

---

## Later, deliberately not now

- **Authentik OIDC** — implement inside `get_current_user()` and the `# AUTH:` hook in
  `HttpApiClient`. Nothing else should need to change; if it does, M2–M6 got the boundary
  wrong.
- **OCR** for scans with no text layer (ocrmypdf/tesseract in the poller).
- **Learning from overrides** — `history_entry.suggestion_snapshot` is already recording the
  training data. Feeding recent corrections back into the prompt is the cheap first version.
