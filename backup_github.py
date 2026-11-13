"""Encrypted database backups in a private GitHub repo (release files, newest KEEP kept).

Once a day maintenance.py calls upload(): the newest backups/personal-*.db is gzipped and
encrypted with AES-256-GCM using the key in .backup_key (never in git; keep a copy in your
password manager), then attached to a release "db-YYYY-MM-DD" in REPO. Release files don't
grow the repo's git history.

File format (.db.gz.enc): b"PAPIBK1\\n" + 12-byte nonce + AES-GCM(ciphertext + tag) of gzip(db).

Restore (on any machine with Python + cryptography):
    python backup_github.py restore personal-2026-11-14.db.gz.enc restored.db --key <key>
"""
import argparse
import base64
import gzip
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

import config

HERE = Path(__file__).resolve().parent
KEY_FILE = HERE / ".backup_key"
REPO = config.get("backup_repo")     # config.json
KEEP = 30
MAGIC = b"PAPIBK1\n"
GH = r"C:\Program Files\GitHub CLI\gh.exe"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def load_key(text: str | None = None) -> bytes:
    raw = (text if text is not None else KEY_FILE.read_text(encoding="ascii")).strip()
    key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    if len(key) != 32:
        raise ValueError("The backup key must be 32 bytes (base64)")
    return key


def make_key() -> str:
    """Create .backup_key once (never overwrites)."""
    if KEY_FILE.exists():
        return "exists"
    text = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    with open(KEY_FILE, "x", encoding="ascii") as fh:
        fh.write(text + "\n")
    return "created"


def encrypt(data: bytes, key: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = secrets.token_bytes(12)
    return MAGIC + nonce + AESGCM(key).encrypt(nonce, gzip.compress(data, 9), MAGIC)


def decrypt(blob: bytes, key: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not blob.startswith(MAGIC):
        raise ValueError("Not a personal-api backup file")
    nonce, body = blob[len(MAGIC):len(MAGIC) + 12], blob[len(MAGIC) + 12:]
    return gzip.decompress(AESGCM(key).decrypt(nonce, body, MAGIC))   # raises if the key is wrong


def gh(*args, timeout=180) -> str:
    r = subprocess.run([GH, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, creationflags=NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:300])
    return r.stdout


def upload(db_file: Path) -> str:
    """Encrypt db_file and attach it to release db-<date>; prune old releases. Returns a status."""
    if not KEY_FILE.exists():
        return "no .backup_key"
    if not REPO:
        return "no backup_repo in config.json"
    day = db_file.stem.replace("personal-", "")
    tag = f"db-{day}"
    out = HERE / "backups" / f"{db_file.name}.gz.enc"
    out.write_bytes(encrypt(db_file.read_bytes(), load_key()))
    try:
        try:
            gh("release", "create", tag, str(out), "--repo", REPO, "--title", f"Database {day}",
               "--notes", f"Encrypted backup of personal.db from {day} (AES-256-GCM, key in .backup_key).")
        except RuntimeError as e:
            if "already exists" not in str(e):
                raise
            gh("release", "upload", tag, str(out), "--repo", REPO, "--clobber")
        tags = [r["tagName"] for r in json.loads(gh("release", "list", "--repo", REPO, "--limit", "200",
                                                     "--json", "tagName"))]
        old = sorted(t for t in tags if t.startswith("db-"))[:-KEEP]
        for t in old:
            gh("release", "delete", t, "--repo", REPO, "--yes", "--cleanup-tag")
        return f"{tag} uploaded ({out.stat().st_size // 1024} KB)" + (f", {len(old)} old removed" if old else "")
    finally:
        out.unlink(missing_ok=True)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("make-key", help="create .backup_key (once)")
    up = sub.add_parser("upload", help="upload one backups/personal-*.db now")
    up.add_argument("file", type=Path)
    rs = sub.add_parser("restore", help="decrypt a downloaded .db.gz.enc")
    rs.add_argument("enc", type=Path)
    rs.add_argument("out", type=Path)
    rs.add_argument("--key", help="the key text (default: read .backup_key)")
    a = p.parse_args()
    if a.cmd == "make-key":
        print(make_key())
    elif a.cmd == "upload":
        print(upload(a.file))
    else:
        if a.out.exists():
            sys.exit(f"{a.out} exists; pick another name")
        a.out.write_bytes(decrypt(a.enc.read_bytes(), load_key(a.key)))
        print(f"restored {a.out}")


if __name__ == "__main__":
    main()
