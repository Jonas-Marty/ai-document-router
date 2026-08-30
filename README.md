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

## Deployment

Two containers behind Docker Compose: `api` (FastAPI under Uvicorn) and `web` (the built
frontend served by nginx, which also proxies `/api` to the API container). Only `web`
publishes a port. The frontend calls a relative `/api/v1`, so that proxy is what connects the
two halves — the browser only ever talks to one origin, which is also why CORS is a
development-only concern.

### First run

From a clean checkout:

```bash
cp .env.example .env
just secret-key          # paste the output into SECRET_KEY in .env
```

Fill in the rest of `.env` — at minimum `WEBDAV_BASE_URL`, `WEBDAV_USERNAME`,
`WEBDAV_PASSWORD`, `WEBDAV_WATCH_FOLDER`, and `APP_BASE_URL` (the public URL the browser will
use). Use a WebDAV **app password**, never the account password. Then:

```bash
just up
```

The first `just up` builds both images, so it takes a few minutes; later runs start in
seconds. The API container applies database migrations on every start before Uvicorn binds, so
there is no separate migration step. Open <http://localhost:8080>.

`DATABASE_URL` is overridden inside the compose file, so the value in `.env` only affects
native development — the container always writes to its volume.

Useful commands:

```bash
just logs     # follow both containers
just down     # stop and remove the containers (the database volume survives)
just build    # rebuild images after pulling new code, then `just up`
```

The AI endpoint, model, and API key are **not** in `.env` — they are configured in the app's
Settings screen, because they are editable at runtime and the key is encrypted at rest.

### Signing in

**Claim the instance immediately after the first deploy.** Until someone registers, the
sign-in screen offers *Create the first account* to anyone who reaches the URL, and that first
account becomes the admin. Registration then closes; set `ALLOW_REGISTRATION=true` if you
want further self-service sign-ups.

Sessions are an HttpOnly cookie (`SESSION_LIFETIME_DAYS`, default 30). Set `APP_BASE_URL` to
the real public URL: it is what makes the cookie `Secure` on https, and what the OIDC redirect
URI is built from.

#### Single sign-on (Authentik)

Optional, one provider, and it must be a **confidential** client — a client ID alone is not
enough. In Authentik, create an OAuth2/OpenID *Provider*:

- Client type: **Confidential**
- Redirect URI: `<APP_BASE_URL>/api/v1/auth/oidc/callback`
- Scopes: `openid`, `email`, `profile` — an email claim is required; a user without one is
  rejected rather than filed under a guessed address

Then put its values in `.env` (or the Dokploy Environment tab):

```bash
OIDC_ISSUER=https://auth.example.com/application/o/document-router/
OIDC_CLIENT_ID=...
OIDC_CLIENT_SECRET=...
OIDC_PROVIDER_NAME=Authentik      # the label on the sign-in button
```

`OIDC_ISSUER` is the provider's *OpenID Configuration Issuer*; everything else is discovered
from `<issuer>/.well-known/openid-configuration`. Restart the API after changing these.

Signing in through the provider matches on its `sub`, falling back to the email address — so
using SSO with the address of an existing password account links the two rather than creating
a second one. If nobody has registered yet, the first person through SSO becomes the admin.

Other providers implementing OIDC discovery should work, but Authentik is the one this was
built against.

### Rotating SECRET_KEY

`SECRET_KEY` is the Fernet key that encrypts the stored AI API key. **Changing it makes the
stored key permanently unreadable** — the app will fail to decrypt it, and it has to be
re-entered in Settings. Nothing else in the database is encrypted, so there is no other data
loss, but do plan the rotation:

1. Have the AI API key to hand (you cannot read it back out of the app — it is write-only).
2. Replace `SECRET_KEY` in `.env` and run `just down && just up`.
3. Re-enter the API key in Settings.

### Backups

The SQLite database is the only thing worth backing up. Documents themselves live on the
WebDAV server and are never stored locally — the app streams them for preview and moves them
server-side, so a lost database costs you the review queue, history, and settings, not any
file.

```bash
just backup              # writes ./backups/app-<timestamp>.db
```

This uses SQLite's own backup API rather than copying `app.db`. The database runs in WAL mode,
where a plain file copy can miss pages that are committed but still only in the `-wal` file —
a backup that looks fine and silently loses recent work. It runs against the live database and
needs no downtime.

To restore, stop the stack, replace `app.db` inside the volume, and start again. The stale
`-wal` and `-shm` sidecars have to go with it — they belong to the database you are replacing:

```bash
just down
docker compose -f deploy/docker-compose.yml run --rm --no-deps \
  --entrypoint sh -v "$PWD/backups:/backups" api \
  -c 'rm -f /data/app.db-wal /data/app.db-shm && cp /backups/app-<timestamp>.db /data/app.db'
just up
```

(`--entrypoint sh` is required: the image's entrypoint runs migrations and starts the API, so
without overriding it the arguments are ignored and you get a running server, not a restore.)

The database lives in the named Docker volume `appdata`. `just down` keeps it; only an
explicit `docker compose -f deploy/docker-compose.yml down -v` destroys it.

### Scaling note

The API runs a **single** Uvicorn worker on purpose. The poller that watches the WebDAV folder
runs in-process, so a second worker would be a second poller ingesting the same documents and
making duplicate LLM calls. If throughput ever becomes a problem, split the poller into its own
process — do not raise the worker count.
