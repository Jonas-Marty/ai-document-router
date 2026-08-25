# syntax=docker/dockerfile:1
# Must stay the first line -- see the note in backend.Dockerfile.
#
# Build context is the repo root -- see deploy/docker-compose.yml (`context: ..`).

# --- build ---------------------------------------------------------------------------------
# Node 24 matches the development environment. Vite 8 requires >=20.19, so this is not a
# free choice down to any older major.
FROM node:24-alpine AS build

# Pinned to the version that produced pnpm-lock.yaml (lockfileVersion 9.0). Installed via npm
# rather than corepack: corepack's signature checks have broken builds on key rotation, and
# this needs no network beyond the registry we are already using.
RUN npm install --global pnpm@11.22.0

WORKDIR /app

# Lockfile-only layer, cached until dependencies actually change. --frozen-lockfile makes the
# build fail rather than silently resolve something new if the lockfile is out of date.
# --store-dir is passed explicitly so it provably matches the cache mount: pnpm's default
# store location varies with how pnpm was installed, and a mount pointing somewhere pnpm
# isn't writing is a cache that silently never hits.
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN --mount=type=cache,target=/pnpm-store \
    pnpm install --frozen-lockfile --store-dir /pnpm-store

COPY frontend/ ./
# `pnpm build` is `tsc -b && vite build` -- a type error fails the image build, which is the
# intent: a broken bundle should never reach a running container.
RUN pnpm build

# --- runtime -------------------------------------------------------------------------------
# nginx stable branch. The default nginx entrypoint runs the master as root and workers as the
# unprivileged `nginx` user, which is the upstream-supported arrangement -- the backend, which
# actually touches persistent data, is the one that runs fully non-root.
FROM nginx:1.28-alpine AS runtime

COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
