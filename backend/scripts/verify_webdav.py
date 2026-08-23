"""Manual M3 verification against a real WebDAV server.

Mocks cannot catch a server that normalizes paths differently than we assumed, or a
PROPFIND shape that differs from the canned XML. This script is the "verify manually
against your actual Nextcloud once" half of M3's acceptance criteria.

Read-only by default -- it lists and stats, nothing else. Pass --write to additionally
exercise mkdir_p and move inside a scratch folder it creates itself. Nothing is ever
deleted, in or out of write mode.

    cd backend && uv run python scripts/verify_webdav.py
    cd backend && uv run python scripts/verify_webdav.py --write /Documents
"""

import argparse
import sys
from pathlib import Path

# Run as a plain script (`uv run python scripts/verify_webdav.py`), so `backend/` needs to
# be importable rather than just this script's own directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings as config  # noqa: E402
from app.services.errors import AppError  # noqa: E402
from app.services.webdav import WebDavService, build_client  # noqa: E402

OK = "\033[32m  ok \033[0m"
BAD = "\033[31mFAIL \033[0m"
INFO = "\033[36m  -- \033[0m"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        metavar="ROOT",
        help="also exercise mkdir_p and move inside a scratch folder under ROOT",
    )
    args = parser.parse_args()

    if not config.webdav_base_url or "example.com" in config.webdav_base_url:
        print(f"{BAD} WEBDAV_BASE_URL is unset or still the placeholder.")
        print(f"{INFO} Fill in the WEBDAV_* values in .env first.")
        return 1

    watch = config.webdav_watch_folder
    permitted = [watch] + ([args.write] if args.write else [])
    service = WebDavService(build_client(), permitted)

    print(f"{INFO} server:       {config.webdav_base_url}")
    print(f"{INFO} watch folder: {watch}")
    print()

    failures = 0

    # 1. Reachability and listing -- proves auth, the base URL, and PROPFIND parsing.
    try:
        entries = service.list_dir(watch)
    except AppError as exc:
        print(f"{BAD} list_dir({watch}): [{exc.code}] {exc.message}")
        print(f"{INFO} Check WEBDAV_USERNAME / WEBDAV_PASSWORD (use an app password) and")
        print(f"{INFO} that {watch} exists on the server.")
        return 1

    print(f"{OK} list_dir({watch}) -> {len(entries)} entries")
    for entry in entries[:5]:
        kind = "dir " if entry.is_dir else "file"
        size = "" if entry.size_bytes is None else f"  {entry.size_bytes} bytes"
        print(f"{INFO}   [{kind}] {entry.path}{size}")
    if len(entries) > 5:
        print(f"{INFO}   ... and {len(entries) - 5} more")

    # 2. Paths round-trip: what the server reported must be usable as input again.
    # This is the assumption most likely to be wrong in a way mocks cannot reveal.
    for entry in entries[:3]:
        try:
            if service.exists(entry.path):
                print(f"{OK} round-trip exists({entry.path})")
            else:
                failures += 1
                print(f"{BAD} round-trip exists({entry.path}) -> False")
                print(f"{INFO} The server listed this path but does not recognise it back.")
        except AppError as exc:
            failures += 1
            print(f"{BAD} round-trip exists({entry.path}): [{exc.code}] {exc.message}")

    # 3. stat on a real file, including any non-ASCII name we happened to find.
    files = [entry for entry in entries if not entry.is_dir]
    if files:
        target = next((f for f in files if not f.name.isascii()), files[0])
        try:
            info = service.stat(target.path)
            print(f"{OK} stat({info.path}) -> {info.size_bytes} bytes, {info.content_type}")
        except AppError as exc:
            failures += 1
            print(f"{BAD} stat({target.path}): [{exc.code}] {exc.message}")
    else:
        print(f"{INFO} no files in the watch folder; skipped stat")
        print(f"{INFO} drop a PDF in {watch} to exercise stat and streaming")

    # 4. Streaming, first chunk only -- confirms open()'s PROPFIND+GET works for real.
    if files:
        try:
            first = next(iter(service.read_stream(files[0].path, chunk_size=64)), b"")
            print(f"{OK} read_stream({files[0].path}) -> first {len(first)} bytes")
        except AppError as exc:
            failures += 1
            print(f"{BAD} read_stream({files[0].path}): [{exc.code}] {exc.message}")

    if args.write:
        source = files[0].path if files else None
        failures += _verify_writes(service, args.write, source)

    print()
    if failures:
        print(f"{BAD} {failures} check(s) failed.")
        return 1
    print(f"{OK} All checks passed. M3 is verified against the real server.")
    if not args.write:
        print(f"{INFO} Reads only. Re-run with --write /Your/Root to cover mkdir_p and move.")
    return 0


def _verify_writes(service: WebDavService, root: str, source: str | None) -> int:
    """Exercise mkdir_p, move, and the no-overwrite guarantee in a scratch folder."""
    from datetime import datetime

    from app.services.errors import WebDAVConflict

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    scratch = f"{root}/_router-verify-{stamp}"
    failures = 0

    print()
    print(f"{INFO} write checks in {scratch}")

    try:
        service.mkdir_p(f"{scratch}/nested/deep")
        print(f"{OK} mkdir_p({scratch}/nested/deep)")
    except AppError as exc:
        print(f"{BAD} mkdir_p: [{exc.code}] {exc.message}")
        return failures + 1

    try:
        service.mkdir_p(f"{scratch}/nested/deep")
        print(f"{OK} mkdir_p is idempotent on an existing tree")
    except AppError as exc:
        failures += 1
        print(f"{BAD} mkdir_p not idempotent: [{exc.code}] {exc.message}")

    if source is None:
        print(f"{INFO} no source file available; skipped move checks")
        return failures

    # Set up via the raw client: the app itself never copies, so this is harness-only.
    raw = build_client()
    first = f"{scratch}/moved-once.pdf"
    second = f"{scratch}/nested/moved-twice.pdf"
    try:
        raw.copy(source.lstrip("/"), first.lstrip("/"))
        print(f"{OK} staged a copy of {source}")
    except Exception as exc:  # noqa: BLE001 - harness setup
        print(f"{BAD} could not stage a copy: {exc}")
        return failures + 1

    try:
        service.move(first, second)
        print(f"{OK} move({first} -> {second})")
    except AppError as exc:
        failures += 1
        print(f"{BAD} move: [{exc.code}] {exc.message}")
        return failures

    # The check that matters most for M5: an occupied destination must never be clobbered.
    try:
        raw.copy(source.lstrip("/"), first.lstrip("/"))
        service.move(first, second)
    except WebDAVConflict:
        print(f"{OK} move onto an occupied destination refused (no overwrite)")
    except AppError as exc:
        failures += 1
        print(f"{BAD} expected a conflict, got [{exc.code}] {exc.message}")
    else:
        failures += 1
        print(f"{BAD} move OVERWROTE an existing file -- CLAUDE.md rule 2 violated")

    print(f"{INFO} scratch folder left in place for you to inspect and remove:")
    print(f"{INFO}   {scratch}")
    return failures


if __name__ == "__main__":
    sys.exit(main())
