# CLAUDE.md

Working agreement for this repository. Read `SPEC.md` before writing code; it is the source
of truth for behaviour. Read `IMPLEMENTATION_PLAN.md` for build order.

## Project

AI Document Router — a self-hosted, single-user tool for reviewing AI-suggested filenames and
destination folders for scanned documents before filing them to a WebDAV store.

Monorepo: `backend/` (FastAPI + SQLite), `frontend/` (Vite + React + TypeScript),
`deploy/` (Docker Compose, deployment only).

## Environment

Development runs **natively in WSL2**, not in containers. The repo must live inside the WSL2
filesystem (`~/projects/...`), never on `/mnt/c` — cross-boundary file watching breaks HMR
and `--reload`.

Docker is used only to verify the deployment build before shipping.

## Commands

Use `just`, never raw commands, so everything stays reproducible:

- `just dev` — backend and frontend together, both with hot reload
- `just dev-api` / `just dev-web` — one side only
- `just test` / `just test-api` / `just test-web`
- `just lint` — Ruff + Biome, both with `--fix`
- `just typecheck` — mypy + tsc
- `just migrate "message"` — generate an Alembic revision
- `just upgrade` — apply migrations
- `just check` — lint + typecheck + test; **run this before declaring any task done**

## Conventions

**Python**
- Ruff for lint and format, line length 100. Type hints on every function signature.
- Route handlers stay thin: validate, call a service, map errors to HTTP. Business logic
  lives in `app/services/`.
- One module owns WebDAV I/O (`services/webdav.py`). Nothing else imports `webdav4`.
- Custom exceptions in services; a single exception handler in `main.py` maps them to the
  error envelope in SPEC §5. Never raise `HTTPException` from a service.
- SQLModel for tables, separate Pydantic schemas for request/response. Do not return ORM
  objects directly from routes.
- Every schema change gets an Alembic migration. No `create_all` outside tests.

**TypeScript**
- Biome for lint and format. `strict: true`. No `any` — if a type is genuinely unknown, use
  `unknown` and narrow.
- No component calls `fetch`. Everything goes through `services/api/` and the hooks in
  `hooks/`.
- TanStack Query owns all server state. No `useEffect` data fetching. No global store —
  local `useState` for UI state is sufficient for this app.
- API response keys are snake_case and stay that way. Do not add a case-transform layer.
- shadcn components are added via the CLI into `components/ui/` and left unmodified; wrap
  them rather than editing them.

**Both**
- Code, comments, commit messages, and identifiers in English.
- Comments explain *why*, not *what*. Delete commented-out code rather than keeping it.

## Rules that are not negotiable

1. **Never delete a file from WebDAV.** Trash means moving to the configured trash folder.
   There is no delete path anywhere in this codebase.
2. **Never overwrite a file on move.** Check for collision immediately before the MOVE and
   fail with 409 rather than clobbering.
3. **Every user-supplied path is validated against `allowed_root_folders` in the service
   layer**, not in the route handler and not only in the frontend. This is the security
   boundary of the app.
4. **The frontend never contacts the LLM provider or the WebDAV server.** Only the backend
   holds credentials.
5. **The API key is write-only.** Never returned by any endpoint, never logged, never in the
   query cache or localStorage.
6. **Validation exists on both sides.** Frontend validation is for feedback; backend
   validation is what actually protects anything.
7. **Never advance the review queue on a failed approve.** The user's edits must survive.
8. No Supabase, Firebase, or any BaaS. No new runtime dependency without asking first.

## Testing

- Backend: pytest. WebDAV is mocked at the `services/webdav.py` boundary — tests never touch
  a real server. LLM calls mocked with respx. Cover: path validation rejecting escapes and
  out-of-root paths, collision handling, revert when the file has moved, and proposal
  response validation.
- Frontend: Vitest + Testing Library. Cover the review form's validation states, the queue
  advancing correctly on success and not advancing on failure, and the sibling list
  refetching when the folder changes.
- A bug fix gets a test that fails before the fix.

## Working style

- Follow `SPEC.md`. Where it is genuinely silent, pick the simpler option, implement it, and
  append a line to `DECISIONS.md` explaining the choice — do not stop to ask about small
  things.
- Where the spec is *contradictory* or a decision has real cost to reverse, ask before
  building.
- Work in the order in `IMPLEMENTATION_PLAN.md`. Finish and verify a milestone before
  starting the next.
- Run `just check` before saying a task is complete. If it fails, fix it rather than
  reporting it.
- Prefer editing existing files over adding new ones. Keep the layout in SPEC §3.
- Build mobile-first. If a layout only works at desktop width, it is not done.
