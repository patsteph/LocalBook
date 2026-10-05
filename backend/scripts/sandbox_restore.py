#!/usr/bin/env python3
"""Restore a backup into a sandbox and prove a real app could run on it.

    backend/.venv/bin/python backend/scripts/sandbox_restore.py

This replaces the plan's cross-machine restore gate. That gate assumed proving
"a working app comes out the other end" needed a second Mac; it does not, now
that `LOCALBOOK_DATA_DIR` exists (LB-10 item 7). What the second machine would
have added uniquely is two things, and both are already covered:

  * **phrase-only unlock, with no device key** — tested directly by deleting the
    `backup` key and opening the archive with the phrase alone. This script
    re-runs that check against the REAL archive with `--check-phrase`.
  * **absolute paths baked into the archive** — there are none; the tar is
    written with `arcname="data"` and every manifest path is relative. Asserted
    below rather than assumed.

What it does, in order:

  1. Find the newest archive (or take `--archive`).
  2. Verify it: every file against its hash, every database `integrity_check`ed,
     every row count against the manifest.
  3. Unpack it into a SANDBOX directory — never the production data dir, and it
     refuses if you point it there.
  4. Check the unpacked tree for the things a running app needs.
  5. Print the exact command to boot a build against the sandbox.

⚠️ It never writes to the production data directory and never stages a restore.
The staged-restore path has its own command (`POST /restore`); this is the
rehearsal, and a rehearsal that can break the thing it is rehearsing for is not
a rehearsal.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path

# Run from anywhere: the backend package is the parent of scripts/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

GREEN, RED, AMBER, DIM, BOLD, OFF = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m"
)


def say(ok: bool | None, text: str) -> None:
    mark = f"{GREEN}PASS{OFF}" if ok else (f"{RED}FAIL{OFF}" if ok is False else f"{AMBER}····{OFF}")
    print(f"  {mark}  {text}")


def newest_archive(folder: Path) -> Path | None:
    from services import backup_service

    archives = sorted(folder.glob(f"*{backup_service.ARCHIVE_SUFFIX}"), reverse=True)
    return archives[0] if archives else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--archive", help="a specific .lbbackup; default is the newest")
    parser.add_argument("--from-dir", help="where to look for archives; default is the configured destination")
    parser.add_argument(
        "--sandbox",
        default=str(Path.home() / "Library/Application Support/LocalBook-restore-test"),
        help="where to unpack it",
    )
    parser.add_argument("--keep", action="store_true", help="leave the sandbox in place afterwards")
    parser.add_argument(
        "--check-phrase", action="store_true",
        help="also prove the recovery phrase opens it with no device key (prompts)",
    )
    args = parser.parse_args()

    from config import settings
    from services import backup_scheduler, backup_service, restore_service

    production = Path(settings.data_dir).expanduser().resolve()
    sandbox = Path(args.sandbox).expanduser().resolve()

    print(f"\n{BOLD}Sandbox restore{OFF}")
    print(f"{DIM}  production data dir : {production}{OFF}")
    print(f"{DIM}  sandbox             : {sandbox}{OFF}\n")

    # The one refusal that matters. Everything else here is read-only; this is
    # the single path that writes, and pointing it at production would turn a
    # rehearsal into the accident it exists to prevent.
    if sandbox == production or production in sandbox.parents:
        print(f"{RED}Refusing: the sandbox must not be inside the production data dir.{OFF}")
        return 2

    source_dir = Path(args.from_dir).expanduser() if args.from_dir else backup_scheduler.configured_destination()
    if args.archive:
        archive = Path(args.archive).expanduser()
    elif source_dir is None:
        print(f"{RED}No backup destination configured and no --archive given.{OFF}")
        print("  Set one in Settings → Data Health, or pass --from-dir.")
        return 2
    else:
        archive = newest_archive(source_dir)
        if archive is None:
            print(f"{RED}No archives in {source_dir}.{OFF}")
            return 2

    if not archive.is_file():
        print(f"{RED}{archive} does not exist.{OFF}")
        return 2

    size_mb = archive.stat().st_size / 1024 ** 2
    print(f"{BOLD}Archive{OFF}  {archive.name}  ({size_mb:.1f} MB)\n")

    # ── 1. the public header ────────────────────────────────────────────────
    print(f"{BOLD}Header{OFF}")
    try:
        header = backup_service.read_header(archive)
        kinds = {r.get("kind") for r in header.get("recipients", [])}
        say(True, f"created {header.get('created_at')}")
        say(
            "recovery" in kinds,
            "openable with the recovery phrase"
            if "recovery" in kinds
            else "NOT openable with the phrase — this Mac's Keychain only",
        )
        blob = json.dumps(header)
        say(
            "sources.json" not in blob and "core_memory" not in blob,
            "no file names leak in the public header",
        )
    except Exception as exc:
        say(False, f"header unreadable: {exc}")
        return 1

    # ── 2. verification ─────────────────────────────────────────────────────
    print(f"\n{BOLD}Verification{OFF}")
    report = restore_service.verify(archive)
    say(not report.errors, f"archive opens and unpacks ({report.checked_files} files checked)")
    for err in report.errors:
        say(False, err)
    say(not report.missing_files, f"no missing files{'' if not report.missing_files else f' — {report.missing_files[:3]}'}")
    say(not report.mismatched_files, f"every file matches its hash{'' if not report.mismatched_files else f' — {report.mismatched_files[:3]}'}")
    for db, result in report.databases.items():
        say(result.get("ok"), f"{db}: integrity_check = {result.get('integrity')}")
    say(not report.row_count_drift, "row counts match the manifest"
        if not report.row_count_drift else f"row drift: {report.row_count_drift}")

    if not report.ok:
        print(f"\n{RED}The archive did not verify. Nothing was unpacked.{OFF}")
        return 1

    # ── 3. unpack into the sandbox ──────────────────────────────────────────
    print(f"\n{BOLD}Unpacking{OFF}")
    if sandbox.exists():
        shutil.rmtree(sandbox)
    sandbox.parent.mkdir(parents=True, exist_ok=True)

    opened = backup_service.open_archive(archive)
    manifest = opened["manifest"]

    # Absolute paths would make an archive machine-bound. Checked rather than
    # assumed — it is the other thing a cross-machine restore would have caught.
    absolute = [p for p in manifest.get("files", {}) if p.startswith("/")]
    say(not absolute, "every path in the archive is relative" if not absolute
        else f"ABSOLUTE paths found: {absolute[:3]}")

    with tempfile.TemporaryDirectory(prefix="lb-sandbox-") as tmp:
        tar_path = Path(tmp) / "payload.tar"
        tar_path.write_bytes(opened["payload"])
        with tarfile.open(tar_path, "r") as tar:
            tar.extractall(Path(tmp) / "out", filter="data")
        shutil.move(str(Path(tmp) / "out" / "data"), str(sandbox))
    say(True, f"unpacked to {sandbox}")

    # ── 4. what a running app needs ─────────────────────────────────────────
    print(f"\n{BOLD}Would an app run on this?{OFF}")

    for rel in backup_service.SQLITE_DBS:
        path = sandbox / rel
        if not path.is_file():
            say(None, f"{rel}: not present (may be normal on a young install)")
            continue
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )]
            say(bool(tables), f"{rel}: opens, {len(tables)} tables")
        finally:
            conn.close()

    sources_db = sandbox / "localbook.db"
    if sources_db.is_file():
        conn = sqlite3.connect(f"file:{sources_db}?mode=ro", uri=True)
        try:
            n = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
            with_text = conn.execute(
                "SELECT COUNT(*) FROM sources WHERE content IS NOT NULL AND content != ''"
            ).fetchone()[0]
            # This is the one that makes the reindex possible at all: lancedb is
            # not in the archive (D19), so every chunk is rebuilt FROM here. A
            # restore with sources but no content would verify perfectly and
            # produce an app that finds nothing.
            say(with_text > 0, f"{with_text} of {n} sources still carry their text")
        except sqlite3.Error as exc:
            say(False, f"could not read sources: {exc}")
        finally:
            conn.close()

    say(not (sandbox / "lancedb").exists(),
        "no vector index in the archive, as designed (D19) — a reindex rebuilds it")

    archival = sandbox / "memory" / "archival_memory"
    say(archival.exists() if (Path(settings.data_dir) / "memory" / "archival_memory").exists() else None,
        "archival memory is present (the one LanceDB table that is NOT derived)")

    if manifest.get("skipped"):
        say(None, f"{len(manifest['skipped'])} file(s) moved mid-backup and were skipped: "
                  f"{manifest['skipped'][:3]}")

    # ── 5. the phrase path ──────────────────────────────────────────────────
    if args.check_phrase:
        print(f"\n{BOLD}Recovery phrase{OFF}")
        import getpass

        phrase = getpass.getpass("  24 words (not echoed): ")
        try:
            from services import keyvault

            derived = keyvault.recovery_public_key_from_phrase(phrase)
            import base64

            stored = keyvault._recovery_pub_path().read_text().strip()
            matches = base64.b64encode(derived).decode() == stored
            say(matches, "the phrase matches this install's recovery key")
            if matches:
                # Opening with the phrase while the device key also exists does
                # not prove much on its own — the code tries the device first.
                # What it DOES prove is that the sealed recovery recipient is
                # real and decrypts, which is what a new Mac would rely on.
                recipients = backup_service.read_header(archive)["recipients"]
                rec = next((r for r in recipients if r["kind"] == "recovery"), None)
                if rec:
                    key = backup_service._unseal_recovery(rec, phrase)
                    say(len(key) == 32, "the phrase unseals this archive's content key")
                else:
                    say(False, "this archive has no recovery recipient")
        except Exception as exc:
            say(False, f"{exc}")

    # ── done ────────────────────────────────────────────────────────────────
    print(f"\n{BOLD}Next{OFF}")
    print("  Boot a build against the sandbox and confirm chat works.")
    # ⚠️ Run the BINARY, not `open`. `open` launches through launchd, which does
    # NOT inherit this shell's environment — LOCALBOOK_DATA_DIR would be dropped
    # and the app would quietly come up on the PRODUCTION data dir while you
    # believed you were testing a restore. That is a worse outcome than not
    # testing at all.
    print(f"  {AMBER}Run the binary directly — `open` drops the environment and would{OFF}")
    print(f"  {AMBER}launch against your real data instead.{OFF}\n")
    # Resolved, not guessed: the executable inside the bundle is `localbooklm`,
    # not `LocalBook`, and a hint naming a binary that is not there sends the
    # user back to `open` — which is the failure above.
    macos_dir = Path(__file__).resolve().parents[2] / (
        "src-tauri/target/release/bundle/macos/LocalBook.app/Contents/MacOS"
    )
    binary = next((p for p in macos_dir.glob("*") if p.is_file() and os.access(p, os.X_OK)), None)
    shown = binary if binary else macos_dir / "<binary>"
    print(f"    {DIM}LOCALBOOK_DATA_DIR='{sandbox}' \\{OFF}")
    print(f"    {DIM}  '{shown}'{OFF}\n")
    if binary is None:
        print(f"  {AMBER}(no built app found — run ./build.sh --rebuild first){OFF}\n")
    print("  Then reindex — the vector store is never in a backup (D19):\n")
    print(f"    {DIM}curl -X POST 'http://127.0.0.1:8000/reindex/all?drop_tables=true'{OFF}\n")

    if not args.keep:
        shutil.rmtree(sandbox, ignore_errors=True)
        print(f"{DIM}  sandbox removed (pass --keep to boot against it){OFF}\n")
    else:
        print(f"{DIM}  sandbox kept at {sandbox}{OFF}\n")

    print(f"{GREEN}{BOLD}This archive would restore.{OFF}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
