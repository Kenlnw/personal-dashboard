"""Housekeeping for personal.db, run after every ActivityWatch sync (see sync_aw.py):

1. Rollup: per-app minutes per local day -> `daily_app` (kept forever).
2. Cleanup: delete raw PC events (app_usage, afk, web, coding) older than RAW_KEEP_DAYS,
   but only for days that are already rolled up.
3. Backup: once a day, a consistent copy of the DB to backups/, keeping the newest BACKUP_KEEP.
4. Off-PC copy: the newest backup also goes to iCloud Drive (iCloudDrive/Backups/personal-api/),
   keeping the newest OFFSITE_KEEP, so a dead drive doesn't take the data with it.
   (Was OneDrive until 2026-11-13; the free OneDrive plan was full.)
5. GitHub: the newest backup, encrypted, as a release file in the private backup repo
   (backup_github.py; newest 30 kept).

Run by hand:   python maintenance.py            (normal run)
               python maintenance.py --rebuild  (re-roll every day that still has raw data)
"""
import argparse
import os
import shutil
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import db
import summary

RAW_KEEP_DAYS = 90
RAW_TYPES = ("app_usage", "afk", "web", "coding")
BACKUP_DIR = Path(__file__).with_name("backups")
BACKUP_KEEP = 14
CLOUD = Path.home() / "iCloudDrive"          # iCloud for Windows (Microsoft Store version)
OFFSITE_DIR = CLOUD / "Backups" / "personal-api"
OFFSITE_KEEP = 30


def rollup_day(conn: sqlite3.Connection, d: date) -> int:
    since, until = summary.local_day_bounds(d)
    rows = [
        (d.isoformat(), dev, app, round(act / 60, 2), round(win / 60, 2))
        for (dev, app), (act, win) in summary.app_seconds(conn, since, until).items()
    ]
    conn.execute("DELETE FROM daily_app WHERE day = ?", (d.isoformat(),))
    conn.executemany(
        "INSERT INTO daily_app (day, device, app, active_minutes, window_minutes) VALUES (?, ?, ?, ?, ?)",
        rows)
    conn.commit()
    return len(rows)


def earliest_raw_day(conn) -> date | None:
    row = conn.execute("SELECT MIN(start) AS s FROM events WHERE type = 'app_usage'").fetchone()
    return datetime.fromisoformat(row["s"]).astimezone().date() if row["s"] else None


def rollup(conn, rebuild: bool = False) -> list[str]:
    """Re-roll every day after the last finished one (`rollup_through`) up to today.
    Today and yesterday are always redone, since their data is still arriving."""
    today = date.today()
    first = earliest_raw_day(conn)
    if first is None:
        return []
    done = db.get_state(conn, "rollup_through")
    start = first if rebuild or not done else max(first, date.fromisoformat(done) + timedelta(days=1))
    start = min(start, today - timedelta(days=1))
    days = []
    d = start
    while d <= today:
        rollup_day(conn, d)
        days.append(d.isoformat())
        d += timedelta(days=1)
    db.set_state(conn, "rollup_through", (today - timedelta(days=2)).isoformat())
    return days


def cleanup(conn) -> int:
    cutoff_day = date.today() - timedelta(days=RAW_KEEP_DAYS)
    done = db.get_state(conn, "rollup_through")
    if not done or date.fromisoformat(done) < cutoff_day - timedelta(days=1):
        return 0          # never delete raw data whose days aren't rolled up yet
    cutoff, _ = summary.local_day_bounds(cutoff_day)
    placeholders = ",".join("?" * len(RAW_TYPES))
    cur = conn.execute(f"DELETE FROM events WHERE type IN ({placeholders}) AND end < ?",
                       (*RAW_TYPES, cutoff.isoformat()))
    conn.commit()
    return cur.rowcount


def backup(conn) -> Path | None:
    today = date.today().isoformat()
    if db.get_state(conn, "last_backup_day") == today:
        return None
    BACKUP_DIR.mkdir(exist_ok=True)
    dest = BACKUP_DIR / f"personal-{today}.db"
    with sqlite3.connect(dest) as out:
        conn.backup(out)          # consistent even while the server is writing
    out.close()
    db.set_state(conn, "last_backup_day", today)
    for old in sorted(BACKUP_DIR.glob("personal-*.db"))[:-BACKUP_KEEP]:
        old.unlink()
    return dest


def offsite() -> str:
    """Copy the newest local backup to iCloud Drive (once per backup file). Never raises."""
    try:
        if not CLOUD.is_dir():
            return "no iCloud Drive folder"
        latest = max(BACKUP_DIR.glob("personal-*.db"), default=None)
        if latest is None:
            return "no local backup yet"
        dest = OFFSITE_DIR / latest.name
        if dest.exists() and dest.stat().st_size == latest.stat().st_size:
            return "already copied"
        OFFSITE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".partial")       # the cloud never uploads a half-written file
        shutil.copy2(latest, tmp)
        tmp.replace(dest)
        for old in sorted(OFFSITE_DIR.glob("personal-*.db"))[:-OFFSITE_KEEP]:
            old.unlink()
        return dest.name
    except OSError as e:
        return f"failed ({e})"


def github_copy(conn) -> str:
    """Once per daily backup: encrypted copy to the private GitHub backup repo (backup_github.py)."""
    latest = max(BACKUP_DIR.glob("personal-*.db"), default=None)
    if latest is None:
        return "no local backup yet"
    if db.get_state(conn, "last_github_backup") == latest.name:
        return "already uploaded"
    try:
        import backup_github
        status = backup_github.upload(latest)
    except Exception as e:                 # offline, gh logged out, ...: try again on the next run
        return f"failed ({type(e).__name__}: {str(e)[:120]})"
    if "uploaded" in status:
        db.set_state(conn, "last_github_backup", latest.name)
    return status


def run(rebuild: bool = False) -> None:
    conn = db.connect()
    try:
        days = rollup(conn, rebuild)
        deleted = cleanup(conn)
        saved = backup(conn)
        copied = offsite()
        pushed = github_copy(conn)
        print(f"rollup: {len(days)} day(s) {days[0] + '..' + days[-1] if days else ''}"
              f" | cleanup: {deleted} raw event(s) older than {RAW_KEEP_DAYS} days deleted"
              f" | backup: {saved.name if saved else 'already done today'} | iCloud: {copied} | GitHub: {pushed}")
    finally:
        conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--rebuild", action="store_true", help="re-roll every day that has raw data")
    run(p.parse_args().rebuild)
