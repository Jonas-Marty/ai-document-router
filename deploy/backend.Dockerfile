# syntax=docker/dockerfile:1
# The syntax directive has to be the very first line: BuildKit ignores it if any comment,
# blank line, or instruction precedes it, which would silently drop the RUN --mount support
# the dependency-cache layers below rely on.
#
# Build context is the repo root -- see deploy/docker-compose.yml (`context: ..`).

# --- build ---------------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS build

# Pinned rather than :latest so a rebuild months from now resolves the same way.
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /bin/uv

# Put the virtualenv outside the project directory. uv would otherwise create /app/.venv,
# which the runtime stage's `COPY backend/app ./app` sits right next to and which would have
# to be copied as part of the source tree; a fixed absolute path keeps dependencies and
# application code in separate, independently cacheable layers.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Dependencies resolve from the lockfile alone, so this layer is cached until the lockfile
# actually changes -- application edits don't trigger a reinstall.
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# --- runtime -------------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Tesseract backs the "classical OCR" method on the review screen's comparison view. Just
# the engine and its language data -- German first, since these are Swiss business
# documents. Deliberately not ocrmypdf: its job is rewriting a PDF to carry a text layer,
# which this app never wants, and it would drag ghostscript, qpdf, unpaper and pngquant in
# for nothing. Pages are rendered by pypdfium2 in-process and handed to the tesseract CLI.
RUN apt-get update \
    && apt-get install --no-install-recommends --yes \
        tesseract-ocr \
        tesseract-ocr-deu \
        tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# Non-root. The uid is fixed so the named volume's ownership stays stable across rebuilds --
# a fresh uid on every build would leave an existing /data unwritable.
RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --home-dir /app --no-create-home app

COPY --from=build /opt/venv /opt/venv

WORKDIR /app
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./alembic.ini
COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Created here, owned by the app user, so that when Docker first populates the empty named
# volume it inherits this ownership. A volume mounted onto a path that doesn't exist in the
# image is created root-owned instead, and the non-root process cannot write the database.
RUN mkdir -p /data && chown app:app /data

USER app
EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
