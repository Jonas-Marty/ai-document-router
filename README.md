# AI Document Router

A self-hosted, single-user tool for reviewing AI-suggested filenames and destination folders
for scanned documents before filing them to a WebDAV store. See `SPEC.md` for the full
specification and `IMPLEMENTATION_PLAN.md` for build order.

## Stack

- **Backend** — Python 3.12, FastAPI, SQLModel, Alembic, SQLite. Dependency manager: `uv`.
- **Frontend** — Vite, React 18, TypeScript, Tailwind, shadcn/ui, TanStack Query. Package
  manager: `pnpm`.
- **Task runner** — `just`. See the `justfile` for every available command.

## WSL2 setup

Development runs natively in WSL2, not in containers. Docker is used only to verify the
deployment build before shipping.

1. **Clone inside the WSL2 filesystem**, e.g. `~/projects/ai-document-router` — never under
   `/mnt/c/...`. Crossing the Windows/Linux filesystem boundary breaks file watching, which
   breaks Vite's HMR and Uvicorn's `--reload`.
2. Install the toolchain (skip anything already present):
   ```bash
   # uv (Python package manager)
   curl --proto '=https' --tlsv1.2 -LsSf https://astral.sh/uv/install.sh | sh

   # just (task runner)
   curl --proto '=https' --tlsv1.2 -sSf https://just.systems/install.sh | bash -s -- --to ~/.local/bin

   # pnpm, via a Node.js installed through nvm
   corepack enable && corepack prepare pnpm@latest --activate
   ```
   Make sure `~/.local/bin` is on your `PATH`.
3. Install dependencies:
   ```bash
   just setup
   ```
4. Copy the environment file and fill it in:
   ```bash
   cp .env.example .env
   ```
5. Apply database migrations (once the data layer exists — see M2 in the implementation plan):
   ```bash
   just upgrade
   ```
6. Start both sides with hot reload:
   ```bash
   just dev
   ```
   Frontend on `http://localhost:5173`, backend on `http://localhost:8000`. The Vite dev
   server proxies `/api` to the backend, so the frontend never needs a separate base URL in
   development.

## Commands

Run `just` with no arguments to list every available command, or see the `justfile` directly.
Notably: `just dev`, `just test`, `just lint`, `just typecheck`, and `just check` (lint +
typecheck + test — run this before considering any task done).
