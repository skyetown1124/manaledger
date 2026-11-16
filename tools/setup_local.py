"""Set up Mana Ledger to run on this computer (Windows, Mac or Linux).

Run it from the project folder with the Python you have installed:
    py tools/setup_local.py        (Windows)
    python3 tools/setup_local.py   (Mac / Linux)
VS Code runs it for you: Terminal > Run Task > "Mana Ledger: set up".

It makes a private Python environment in .venv, installs the packages, brings the
database up to date, and the first time only, loads your newest
manaledger-backup-*.json (from this folder or your Downloads folder) so your
cards and decks come over from the Codespace. Safe to run again any time.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
VPY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
MARKER = ROOT / ".data-loaded"


def run(*args):
    print("  >", " ".join(str(a) for a in args), flush=True)
    subprocess.run([str(a) for a in args], cwd=ROOT, check=True)


def _has_data():
    db = ROOT / "db.sqlite3"
    if not db.exists():
        return False
    import sqlite3
    try:
        with sqlite3.connect(db) as conn:
            return conn.execute("SELECT COUNT(*) FROM collection_collectionitem").fetchone()[0] > 0
    except sqlite3.Error:
        return False


def main():
    if sys.version_info < (3, 10):
        sys.exit("Mana Ledger needs Python 3.10 or newer. Install it from https://www.python.org/downloads/")

    if not VPY.exists():
        print("Making a private Python environment in .venv (first time only)...")
        run(sys.executable, "-m", "venv", VENV)

    req, stamp = ROOT / "requirements.txt", VENV / "requirements.installed"
    if not stamp.exists() or stamp.read_bytes() != req.read_bytes():
        print("Installing the packages Mana Ledger needs (a minute or two the first time)...")
        run(VPY, "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", req)
        shutil.copyfile(req, stamp)

    print("Updating the database...")
    run(VPY, "manage.py", "migrate", "--noinput", "-v0")

    if not MARKER.exists() and _has_data():
        MARKER.write_text("existing database kept")  # never load a backup over cards you already have
    if not MARKER.exists():
        places = [ROOT, Path.home() / "Downloads"]
        backups = sorted((f for p in places if p.is_dir() for f in p.glob("manaledger-backup-*.json")),
                         key=lambda f: f.stat().st_mtime, reverse=True)
        if backups:
            print(f"Loading your cards and decks from {backups[0]} ...")
            run(VPY, "manage.py", "loaddata", backups[0])
            MARKER.write_text(str(backups[0]))
        else:
            print("\nNo backup file found, so Mana Ledger starts empty.")
            print("To bring your cards over later: in the Codespace's Mana Ledger open Backup & database,")
            print("press Download backup, then restore that file on the same page here.")
            MARKER.write_text("none")

    print("\nAll set. In VS Code press F5 (or Terminal > Run Task > \"Mana Ledger: run\"),")
    print("then open http://127.0.0.1:8000/")


if __name__ == "__main__":
    main()
