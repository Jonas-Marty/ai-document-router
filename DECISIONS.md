# Decisions

Small choices made where `SPEC.md` was silent, or where a fix was needed to keep `just check`
meaningful. One line of "why" each; not a full ADR log.

- **`typecheck` runs `tsc -b` instead of `tsc --noEmit`.** The frontend uses TS project
  references (`tsconfig.json` → `tsconfig.app.json` / `tsconfig.node.json`) with an empty root
  `files: []`. Plain `tsc --noEmit` at the root matches zero files and silently "passes"
  without checking anything. `tsc -b` builds the referenced projects and actually
  typechecks `src/`; each sub-project already sets `noEmit: true` so nothing is emitted.
- **shadcn/ui initialised with the Radix UI component library and the Nova preset** (Lucide
  icons, matching the `lucide-react` dependency SPEC §2 already calls for). SPEC doesn't pin a
  primitives library or preset; Radix is the long-established, most-documented choice for
  shadcn.
- **Font swapped from the Nova preset's default (Geist) to Inter**, per SPEC §9 ("shadcn/ui
  defaults with Inter"). Self-hosted via `@fontsource-variable/inter`, consistent with how the
  preset already self-hosts its default font rather than pulling from a CDN.
- **Tailwind v4** (CSS-based config, `@tailwindcss/vite` plugin, no `tailwind.config.js`).
  SPEC doesn't pin a major version; v4 is what the current shadcn CLI sets up for a Vite
  project.
- **TypeScript 7.0** and **Vite 8**, i.e. current latest stable rather than the versions
  implied by an older toolchain snapshot. SPEC pins React to 18 explicitly (installed as
  such) but doesn't pin these; picked latest stable since this is a new project with no
  compatibility burden.
- **`CORS_ORIGINS` is comma-separated** in `.env` (e.g. `http://localhost:5173,http://x`),
  parsed by a `field_validator` in `app/config.py`. pydantic-settings' default for `list[str]`
  env vars expects JSON; comma-separated is friendlier to hand-edit in a `.env` file.
- **Root `justfile` sets `set tempdir := "/tmp"`.** `just`'s default scratch location for
  shebang recipes (`dev`, etc.) is `$XDG_RUNTIME_DIR`, which is mounted `noexec` in this WSL2
  environment — every shebang recipe failed with "Permission denied (os error 13)" until this
  was pinned to `/tmp`. Harmless on any POSIX system.
- **`frontend/tsconfig.json` (the root, reference-only file) duplicates the `@/*` path alias**
  that already lives in `tsconfig.app.json`. The shadcn CLI reads the root `tsconfig.json`
  directly rather than following its `references`, and without a `paths` entry there it
  silently wrote new components into a literal `./@/...` directory instead of `src/...`.
  Keep both in sync if the alias ever changes.
