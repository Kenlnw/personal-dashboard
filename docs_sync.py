"""Snapshot counts and releases of the GitHub docs repos into personal.db.

Only counts and metadata are stored (file counts, report/note paths, release titles),
never file contents. Uses the GitHub CLI's login (`gh auth login`), so private repos work.

Runs from sync_aw.py at most every MIN_INTERVAL. By hand:
    python docs_sync.py          (respects MIN_INTERVAL)
    python docs_sync.py --force  (sync now)
"""
import argparse
import json
import os
import shutil
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import PurePosixPath
from urllib.parse import quote

import config
import db

# repo -> what it mainly holds
REPOS = config.get("docs_repos")       # config.json
REPORT_EXTS = {".tex", ".typ"}
NOTE_EXTS = {".md"}
TEMPLATE_DIRS = {"_templates", "templates"}
MIN_INTERVAL = timedelta(hours=3)
GH = shutil.which("gh") or r"C:\Program Files\GitHub CLI\gh.exe"


def gh(path: str):
    """GET a GitHub API path with the gh CLI's credentials."""
    out = subprocess.run(
        [GH, "api", path], capture_output=True, text=True, encoding="utf-8", timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)  # no console flash under pythonw
    if out.returncode:
        raise RuntimeError(f"gh api {path}: {out.stderr.strip()}")
    return json.loads(out.stdout)


def hidden(p: PurePosixPath) -> bool:
    return any(part.startswith(".") for part in p.parts)   # .obsidian, .vscode, .github ...


def scan(blobs: list[PurePosixPath]) -> dict:
    """Count visible files; find report folders and notes."""
    visible = [p for p in blobs if not hidden(p)]
    by_ext: dict[str, int] = {}
    for p in visible:
        ext = p.suffix.lower() or "(none)"
        by_ext[ext] = by_ext.get(ext, 0) + 1

    # a report = a folder holding a .tex/.typ file, outside template folders;
    # sub-folders of a report (e.g. sections/) don't count as extra reports
    dirs = sorted({p.parent for p in visible if p.suffix.lower() in REPORT_EXTS
                   and not (set(p.parts) & TEMPLATE_DIRS)}, key=lambda d: len(d.parts))
    reports: list[PurePosixPath] = []
    for d in dirs:
        if not any(r == d or r in d.parents for r in reports if str(r) != "."):
            reports.append(d)
    notes = [p for p in visible if p.suffix.lower() in NOTE_EXTS and p.stem.lower() != "readme"]
    return {"files": len(visible), "by_ext": dict(sorted(by_ext.items(), key=lambda kv: -kv[1])),
            "reports": reports, "notes": notes}


def first_commit_date(repo: str, path: str) -> str | None:
    """Local date of the oldest commit touching `path` (pages through history)."""
    page, oldest = 1, None
    while True:
        commits = gh(f"repos/{repo}/commits?path={quote(path)}&per_page=100&page={page}")
        if not commits:
            break
        oldest = commits[-1]["commit"]["committer"]["date"]
        if len(commits) < 100:
            break
        page += 1
    if not oldest:
        return None
    return datetime.fromisoformat(oldest.replace("Z", "+00:00")).astimezone().date().isoformat()


def sync_repo(conn, repo: str, today: str) -> dict:
    branch = gh(f"repos/{repo}")["default_branch"]
    tree = gh(f"repos/{repo}/git/trees/{branch}?recursive=1")
    blobs = [PurePosixPath(t["path"]) for t in tree["tree"] if t["type"] == "blob"]
    s = scan(blobs)

    items = [(str(p), "report", str(p.parent)) for p in s["reports"]] + \
            [(str(p), "note", str(p.parent)) for p in s["notes"]]
    known = {r["path"]: r["first_seen"] for r in
             conn.execute("SELECT path, first_seen FROM docs_items WHERE repo = ?", (repo,))}
    for path, kind, grp in items:
        first = known.get(path) or first_commit_date(repo, path)   # backfilled once per item
        conn.execute(
            "INSERT INTO docs_items (repo, path, kind, grp, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (repo, path) DO UPDATE SET kind = excluded.kind, grp = excluded.grp, "
            "first_seen = COALESCE(docs_items.first_seen, excluded.first_seen), last_seen = excluded.last_seen",
            (repo, path, kind, grp, first, today))

    releases = gh(f"repos/{repo}/releases?per_page=100")
    for r in releases:
        assets = [{"name": a["name"], "size": a["size"], "downloads": a["download_count"]} for a in r["assets"]]
        conn.execute(
            "INSERT INTO docs_releases (repo, tag, name, published_at, url, assets) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (repo, tag) DO UPDATE SET name = excluded.name, published_at = excluded.published_at, "
            "url = excluded.url, assets = excluded.assets",
            (repo, r["tag_name"], r["name"], r["published_at"], r["html_url"], json.dumps(assets, ensure_ascii=False)))
    tags = [r["tag_name"] for r in releases]
    conn.execute(f"DELETE FROM docs_releases WHERE repo = ? AND tag NOT IN ({','.join('?' * len(tags)) or 'NULL'})",
                 (repo, *tags))

    groups: dict[str, int] = {}
    for _path, _kind, grp in items:
        groups[grp] = groups.get(grp, 0) + 1
    detail = {"by_ext": s["by_ext"], "per_group": groups, "truncated": tree.get("truncated", False)}
    conn.execute(
        "INSERT INTO docs_snapshot (day, repo, files, reports, notes, releases, detail) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (day, repo) DO UPDATE SET files = excluded.files, reports = excluded.reports, "
        "notes = excluded.notes, releases = excluded.releases, detail = excluded.detail",
        (today, repo, s["files"], len(s["reports"]), len(s["notes"]), len(releases),
         json.dumps(detail, ensure_ascii=False)))
    conn.commit()
    return {"files": s["files"], "reports": len(s["reports"]), "notes": len(s["notes"]), "releases": len(releases)}


def sync_github_repos(conn, now: datetime) -> int:
    """Metadata of every repo the account owns or collaborates on (no contents)."""
    me = gh("user")["login"]
    repos, page = [], 1
    while True:
        batch = gh(f"user/repos?affiliation=owner,collaborator,organization_member&per_page=100&page={page}")
        repos += batch
        if len(batch) < 100:
            break
        page += 1
    stamp = now.isoformat()
    for r in repos:
        conn.execute(
            "INSERT INTO github_repos (repo, description, private, fork, archived, owned, language, stars, forks, "
            "open_issues, topics, homepage, url, size_kb, created_at, pushed_at, synced_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (repo) DO UPDATE SET description = excluded.description, private = excluded.private, "
            "fork = excluded.fork, archived = excluded.archived, owned = excluded.owned, language = excluded.language, "
            "stars = excluded.stars, forks = excluded.forks, open_issues = excluded.open_issues, topics = excluded.topics, "
            "homepage = excluded.homepage, url = excluded.url, size_kb = excluded.size_kb, "
            "created_at = excluded.created_at, pushed_at = excluded.pushed_at, synced_at = excluded.synced_at",
            (r["full_name"], r.get("description"), int(r["private"]), int(r["fork"]), int(r["archived"]),
             int(r["owner"]["login"].lower() == me.lower()), r.get("language"), r["stargazers_count"],
             r["forks_count"], r["open_issues_count"], json.dumps(r.get("topics", []), ensure_ascii=False),
             r.get("homepage") or None, r["html_url"], r.get("size"), r.get("created_at"), r.get("pushed_at"), stamp))
    # repos deleted on GitHub (or access removed) disappear from the list
    conn.execute("DELETE FROM github_repos WHERE synced_at != ?", (stamp,))
    conn.commit()
    return len(repos)


def run(force: bool = False) -> None:
    conn = db.connect()
    try:
        last = db.get_state(conn, "docs_last_sync")
        now = datetime.now(timezone.utc)
        if not force and last and now - datetime.fromisoformat(last) < MIN_INTERVAL:
            print("docs: synced recently, skipping")
            return
        try:
            print(f"github repos: {sync_github_repos(conn, now)}")
        except Exception as e:
            print(f"github repos: FAILED {e}")
        today = date.today().isoformat()
        for repo in REPOS:
            try:
                print(f"docs {repo}: {sync_repo(conn, repo, today)}")
            except Exception as e:      # one failing repo shouldn't stop the others
                print(f"docs {repo}: FAILED {e}")
        db.set_state(conn, "docs_last_sync", now.isoformat())
    finally:
        conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--force", action="store_true", help="sync even if done recently")
    run(p.parse_args().force)
