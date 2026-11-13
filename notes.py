"""Notes (read-only browser over the docs repos) and search across everything.

Roots are the local clones the workspaces can link (PersonalDocs = the Obsidian vault,
LaTeX_docs, Typst_docs). The dashboard only reads them: git and Obsidian own the files.

Search covers notes (file names + text of Markdown / LaTeX / Typst / text files), tasks,
workspaces, workspace chats and Studio outputs, exams and money entries. Every query term
must match (AND), case-insensitively; results are grouped by kind.
"""
import json
import mimetypes
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import workspaces as W
from tasks import get_conn

SKIP = W.SKIP_DIRS | {".claude", ".github"}
TEXT_EXTS = {".md", ".markdown", ".txt", ".tex", ".typ", ".bib", ".csv", ".tsv", ".json", ".yaml", ".yml",
             ".base", ".canvas", ".py", ".js", ".css", ".html", ".sty", ".cls", ".log", ".toml", ".ini"}
SEARCH_EXTS = {".md", ".markdown", ".txt", ".tex", ".typ", ".bib"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".heic"}
MAX_TEXT = 2_000_000
CACHE_TTL = 30            # seconds a folder walk stays fresh


def roots() -> dict[str, Path]:
    return {re.sub(r"[^a-z0-9]+", "", p.name.lower()): p for p in W.LINK_ROOTS if p.exists()}


def root_path(root: str) -> Path:
    p = roots().get(root)
    if not p:
        raise HTTPException(404, "No such notes folder")
    return p


def safe(root: str, rel: str) -> Path:
    base = root_path(root).resolve()
    p = (base / (rel or "")).resolve()
    if p != base and base not in p.parents:
        raise HTTPException(400, "Path is outside the notes folder")
    if any(part in SKIP for part in p.relative_to(base).parts):
        raise HTTPException(404, "Not found")
    return p


_walks: dict[str, tuple[float, list[dict]]] = {}
_texts: dict[str, tuple[float, str]] = {}          # absolute path -> (mtime, text)
_lock = threading.Lock()


def walk(root: str) -> list[dict]:
    """Every file under the root (skipping .git, .obsidian, ...), cached for CACHE_TTL seconds."""
    with _lock:
        hit = _walks.get(root)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1]
    base = root_path(root)
    out, stack = [], [base]
    while stack:
        d = stack.pop()
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for e in entries:
            if e.name in SKIP or e.name.startswith("."):
                continue
            if e.is_dir():
                stack.append(e)
            elif e.is_file():
                st = e.stat()
                out.append({"path": e.relative_to(base).as_posix(), "size": st.st_size, "mtime": st.st_mtime})
    out.sort(key=lambda f: f["path"].lower())
    with _lock:
        _walks[root] = (time.time(), out)
    return out


def forget(root: str) -> None:
    """Drop the cached folder listing (after a git pull changed files)."""
    with _lock:
        _walks.pop(root, None)


def read_text(p: Path, mtime: float) -> str:
    key = str(p)
    hit = _texts.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        raw = p.read_bytes()[:MAX_TEXT]
    except OSError:
        return ""
    text = raw.decode("utf-8", errors="replace")
    _texts[key] = (mtime, text)
    return text


def kind_of(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext in (".md", ".markdown"):
        return "md"
    if ext == ".pdf":
        return "pdf"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in TEXT_EXTS:
        return "text"
    return "other"


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="minutes")


# ---------------------------------------------------------------- search

def terms_of(q: str) -> list[str]:
    return [t.casefold() for t in q.split() if t.strip()][:8]


_lows: dict[str, tuple[float, str, int]] = {}      # absolute path -> (mtime, casefolded text, body start)


def read_low(p: Path, mtime: float) -> tuple[str, int]:
    """Casefolded text (cached until the file changes) and where the body starts (after properties)."""
    key = str(p)
    hit = _lows.get(key)
    if hit and hit[0] == mtime:
        return hit[1], hit[2]
    text = read_text(p, mtime)
    start = 0
    if p.suffix.lower() in (".md", ".markdown") and text.startswith("---"):
        end = text.find("\n---", 3)
        start = end + 4 if end > 0 else 0
    low = text.casefold()
    if len(low) != len(text):          # rare characters change length when casefolded: lower() keeps positions
        low = text.lower()
    _lows[key] = (mtime, low, start)
    return low, start


def window(text: str, low: str, terms: list[str], start: int = 0, span: int = 400) -> str:
    """A short piece of raw text around the first hit (cheap: never processes the whole file)."""
    pos = min((p for p in (low.find(t, start) for t in terms) if p >= 0), default=-1)
    if pos < 0:
        return text[start: start + span]
    a = max(start, pos - span // 2)
    return text[a: pos + span]


def snippet(text: str, terms: list[str], width: int = 70) -> str:
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)                  # hidden comments
    text = re.sub(r"!?\[\[([^\]|]*\|)?([^\]]*)\]\]", r"\2", text)          # [[link|label]] -> label
    text = re.sub(r"(^|\s)#{1,6}\s|\*\*|__|==|`", " ", text)              # heading marks, bold, highlights
    low = text.casefold()
    pos = min((low.find(t) for t in terms if low.find(t) >= 0), default=-1)
    if pos < 0:
        s = text[: width * 2]
    else:
        start = max(0, pos - width)
        s = ("…" if start else "") + text[start: pos + width * 2]
    s = re.sub(r"\s+", " ", s).strip()
    return s[: width * 3] + ("…" if len(s) > width * 3 else "")


def matches(text: str, terms: list[str]) -> bool:
    low = text.casefold()
    return all(t in low for t in terms)


def like_all(cols: list[str], terms: list[str]) -> tuple[str, list]:
    """SQL: every term appears in at least one of the columns."""
    parts, args = [], []
    for t in terms:
        parts.append("(" + " OR ".join(f"COALESCE({c}, '') LIKE ?" for c in cols) + ")")
        args += [f"%{t}%"] * len(cols)
    return " AND ".join(parts) or "1", args


def search_notes(terms: list[str], limit: int) -> tuple[list[dict], int]:
    hits = []
    for rid, base in roots().items():
        for f in walk(rid):
            path_low = f["path"].casefold()
            ext = f["path"][f["path"].rfind("."):].lower() if "." in f["path"] else ""
            searchable = ext in SEARCH_EXTS and f["size"] <= MAX_TEXT
            if not all(t in path_low for t in terms):           # every term in the path or in the text
                if not searchable:
                    continue
                low, start = read_low(base / f["path"], f["mtime"])
                if not all(t in path_low or t in low for t in terms):
                    continue
            elif searchable:
                low, start = read_low(base / f["path"], f["mtime"])
            else:
                low, start = "", 0
            name = Path(f["path"]).stem
            text = read_text(base / f["path"], f["mtime"]) if searchable else ""
            score = ((10 if all(t in name.casefold() for t in terms) else 0) + (3 if ext in (".md", ".markdown") else 0)
                     + min(sum(low.count(t, start) for t in terms), 20) + f["mtime"] / 1e10)
            parent = Path(f["path"]).parent.as_posix()
            hits.append({"kind": "note", "title": name, "sub": base.name + (f" / {parent}" if parent != "." else ""),
                         "snippet": snippet(window(text, low, terms, start), terms) if text else "", "date": iso(f["mtime"])[:10],
                         "href": f"#/notes/{rid}?p={quote(f['path'])}", "score": score, "type": kind_of(f["path"])})
    hits.sort(key=lambda h: -h["score"])
    return hits[:limit], len(hits)


def search_db(conn, terms: list[str], limit: int) -> dict:
    out = {}
    w, a = like_all(["t.title", "t.description"], terms)
    rows = conn.execute(f"SELECT t.id, t.title, t.description, t.status, t.due, t.updated_at, w.name AS ws FROM tasks t "
                        f"LEFT JOIN workspaces w ON w.slug = t.workspace WHERE {w} "
                        "ORDER BY t.status IN ('done', 'canceled'), t.updated_at DESC", a).fetchall()
    out["tasks"] = ([{"kind": "task", "title": r["title"], "sub": f"T-{r['id']} · {r['status'].replace('_', ' ')}"
                      + (f" · {r['ws']}" if r["ws"] else "") + (f" · due {r['due']}" if r["due"] else ""),
                      "snippet": snippet(r["description"] or "", terms) if r["description"] else "",
                      "date": (r["updated_at"] or "")[:10], "href": f"#/tasks/{r['id']}"} for r in rows[:limit]], len(rows))

    w, a = like_all(["name", "description"], terms)
    rows = conn.execute(f"SELECT slug, name, description FROM workspaces WHERE {w} ORDER BY name", a).fetchall()
    out["workspaces"] = ([{"kind": "workspace", "title": r["name"], "sub": "Workspace", "snippet": r["description"],
                           "href": f"#/workspaces/{r['slug']}"} for r in rows[:limit]], len(rows))

    w, a = like_all(["m.content"], terms)
    rows = conn.execute(f"SELECT m.ws, m.day, m.role, m.content, w.name FROM ws_messages m JOIN workspaces w ON w.slug = m.ws "
                        f"WHERE m.role IN ('user', 'assistant') AND {w} ORDER BY m.id DESC", a).fetchall()
    out["chats"] = ([{"kind": "chat", "title": f"{r['name']} · {r['day']}", "sub": "You asked" if r["role"] == "user" else "Claude answered",
                      "snippet": snippet(r["content"], terms), "date": r["day"], "href": f"#/workspaces/{r['ws']}"}
                     for r in rows[:limit]], len(rows))

    w, a = like_all(["e.title", "e.scope", "e.reflection"], terms)
    rows = conn.execute(f"SELECT e.*, w.name AS ws FROM exams e LEFT JOIN workspaces w ON w.slug = e.workspace WHERE {w} "
                        "ORDER BY e.date DESC", a).fetchall()
    out["exams"] = ([{"kind": "exam", "title": r["title"], "sub": f"{r['kind'].title()} · {r['date']}" + (f" · {r['ws']}" if r["ws"] else ""),
                      "snippet": r["scope"], "date": r["date"],
                      "href": f"#/workspaces/{r['workspace']}" if r["workspace"] else "#/focus"} for r in rows[:limit]], len(rows))

    w, a = like_all(["t.note", "c.name", "a.name"], terms)
    rows = conn.execute(f"SELECT t.*, c.name AS cat, a.name AS acc FROM money_tx t JOIN money_accounts a ON a.id = t.account_id "
                        f"LEFT JOIN money_categories c ON c.id = t.category_id WHERE t.type != 'adjust' AND {w} "
                        "ORDER BY t.date DESC, t.id DESC", a).fetchall()
    out["money"] = ([{"kind": "money", "title": f"{'−' if r['type'] == 'expense' else '+' if r['type'] == 'income' else ''}¥{r['amount']:,}"
                      + (f" · {r['cat']}" if r["cat"] else " · transfer" if r["type"] == "transfer" else ""),
                      "sub": f"{r['acc']} · {r['date']}", "snippet": r["note"], "date": r["date"],
                      "href": f"#/money/{r['date'][:7]}"} for r in rows[:limit]], len(rows))
    return out


def search_outputs(conn, terms: list[str], limit: int) -> tuple[list[dict], int]:
    names = {r["slug"]: r["name"] for r in conn.execute("SELECT slug, name FROM workspaces")}
    hits = []
    for slug, name in names.items():
        folder = W.WS_ROOT / slug / "outputs"
        if not folder.exists():
            continue
        for p in folder.iterdir():
            if not p.is_file() or p.suffix.lower() not in (".md", ".json"):
                continue
            st = p.stat()
            text = read_text(p, st.st_mtime)
            if p.suffix == ".json":          # flashcards / quiz / plan: search the visible text only
                try:
                    text = " ".join(str(v) for v in _strings(json.loads(text)))
                except ValueError:
                    pass
            if matches(p.stem + "\n" + text, terms):
                hits.append({"kind": "output", "title": re.sub(r" \(\d{4}-\d{2}-\d{2} \d{4}\)", "", p.name.split(".")[0]),
                             "sub": f"{name} · Studio", "snippet": snippet(text, terms), "date": iso(st.st_mtime)[:10],
                             "href": f"#/workspaces/{slug}", "mtime": st.st_mtime})
    hits.sort(key=lambda h: -h.pop("mtime"))
    return hits[:limit], len(hits)


def _strings(v):
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for x in v.values():
            yield from _strings(x)
    elif isinstance(v, list):
        for x in v:
            yield from _strings(x)


# ---------------------------------------------------------------- links + writing new notes

VAULT = "personaldocs"          # the Obsidian vault: the only place the dashboard writes (new files only)
INBOX = "Inbox"


def locate(abs_path: str) -> tuple[str, str] | None:
    """(root id, relative posix path) for an absolute path inside one of the roots."""
    p = Path(abs_path).resolve()
    for rid, base in roots().items():
        b = base.resolve()
        if p == b or b in p.parents:
            return rid, "" if p == b else p.relative_to(b).as_posix()
    return None


def workspaces_for(conn, root: str, rel: str) -> list[dict]:
    """Workspaces whose linked folder contains this file / folder."""
    out = []
    for f in conn.execute("SELECT f.path, w.slug, w.name FROM ws_folders f JOIN workspaces w ON w.slug = f.ws"):
        loc = locate(f["path"])
        if loc and loc[0] == root and (not loc[1] or rel == loc[1] or rel.startswith(loc[1] + "/")):
            if not any(o["slug"] == f["slug"] for o in out):
                out.append({"slug": f["slug"], "name": f["name"]})
    return out


def clean_name(title: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f#^\[\]]+', " ", title)
    name = re.sub(r"\s+", " ", name).strip(" .")[:80]
    return name or datetime.now().strftime("Note %Y-%m-%d %H%M")


def write_new(folder_rel: str, title: str, text: str) -> str:
    """Create <vault>/<folder>/<title>.md without ever overwriting; returns the relative path."""
    base = root_path(VAULT).resolve()
    folder = safe(VAULT, folder_rel)
    if folder == base and folder_rel.strip("/"):
        raise HTTPException(400, "Bad folder")
    folder.mkdir(parents=True, exist_ok=True)
    stem, n = clean_name(title), 1
    while True:
        p = folder / (f"{stem}.md" if n == 1 else f"{stem} ({n}).md")
        try:
            with open(p, "x", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            break
        except FileExistsError:
            n += 1
    with _lock:
        _walks.pop(VAULT, None)
    return p.relative_to(base).as_posix()


# ---------------------------------------------------------------- API

def build_router(check_token) -> APIRouter:
    r = APIRouter(dependencies=[Depends(check_token)])

    @r.get("/notes")
    def home():
        out, recent = [], []
        for rid, base in roots().items():
            files = walk(rid)
            out.append({"id": rid, "name": base.name, "files": len(files),
                        "notes": sum(1 for f in files if kind_of(f["path"]) == "md"),
                        "pdfs": sum(1 for f in files if kind_of(f["path"]) == "pdf"),
                        "modified": iso(max((f["mtime"] for f in files), default=0)) if files else None})
            recent += [{"root": rid, "root_name": base.name, **f} for f in files if kind_of(f["path"]) in ("md", "pdf")]
        recent.sort(key=lambda f: -f["mtime"])
        return {"roots": out, "recent": [{**f, "modified": iso(f["mtime"])} for f in recent[:15]]}

    @r.get("/notes/{root}/tree")
    def tree(root: str, path: str = ""):
        d = safe(root, path)
        if not d.is_dir():
            raise HTTPException(404, "Not a folder")
        rel = "" if d == root_path(root).resolve() else d.relative_to(root_path(root).resolve()).as_posix()
        pre = rel + "/" if rel else ""
        dirs, files = {}, []
        for f in walk(root):
            if not f["path"].startswith(pre):
                continue
            rest = f["path"][len(pre):]
            if "/" in rest:
                name = rest.split("/", 1)[0]
                e = dirs.setdefault(name, {"name": name, "path": pre + name, "files": 0, "mtime": 0})
                e["files"] += 1
                e["mtime"] = max(e["mtime"], f["mtime"])
            else:
                files.append({**f, "name": rest, "kind": kind_of(rest), "modified": iso(f["mtime"])})
        return {"root": root, "root_name": root_path(root).name, "path": rel,
                "dirs": [{**v, "modified": iso(v["mtime"])} for v in sorted(dirs.values(), key=lambda x: x["name"].lower())],
                "files": files}

    @r.get("/notes/{root}/index")
    def index(root: str):
        """All file paths, for resolving [[wiki links]] and ![[embeds]] by name."""
        return [f["path"] for f in walk(root)]

    @r.get("/notes/{root}/file")
    def file(root: str, path: str, conn=Depends(get_conn)):
        p = safe(root, path)
        if not p.is_file():
            raise HTTPException(404, "No such file")
        st = p.stat()
        rel = p.relative_to(root_path(root).resolve()).as_posix()
        k = kind_of(rel)
        out = {"root": root, "root_name": root_path(root).name, "path": rel, "name": p.name, "kind": k,
               "size": st.st_size, "modified": iso(st.st_mtime), "workspaces": workspaces_for(conn, root, rel)}
        if k in ("md", "text"):
            out["content"] = read_text(p, st.st_mtime)
            out["truncated"] = st.st_size > MAX_TEXT
        if k == "md":                       # backlinks: notes that link here with [[name]]
            stem = re.escape(p.stem)
            pat = re.compile(rf"\[\[(?:[^\]|#]*/)?{stem}(?:\.md)?(?:[#|][^\]]*)?\]\]", re.I)
            base = root_path(root)
            out["backlinks"] = [{"path": f["path"], "name": Path(f["path"]).stem} for f in walk(root)
                                if f["path"] != rel and kind_of(f["path"]) == "md"
                                and p.stem.casefold() in read_low(base / f["path"], f["mtime"])[0]   # cheap pre-check
                                and pat.search(read_text(base / f["path"], f["mtime"]))][:50]
        return out

    @r.get("/notes/{root}/raw")
    def raw(root: str, path: str):
        p = safe(root, path)
        if not p.is_file():
            raise HTTPException(404, "No such file")
        media = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=60"}
        if media != "application/pdf":      # an HTML / SVG file opened directly can't run scripts
            headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'"
        return FileResponse(p, media_type=media, headers=headers, filename=p.name, content_disposition_type="inline")

    @r.get("/search")
    def search(q: str = Query("", max_length=200), limit: int = 20, conn=Depends(get_conn)):
        terms = terms_of(q)
        if not terms:
            return {"q": q, "groups": [], "total": 0}
        limit = max(1, min(limit, 100))
        t0 = time.time()
        groups = {"notes": search_notes(terms, limit), **search_db(conn, terms, limit),
                  "outputs": search_outputs(conn, terms, limit)}
        labels = {"notes": "Notes", "tasks": "Tasks", "workspaces": "Workspaces", "outputs": "Study material",
                  "chats": "Workspace chats", "exams": "Exams", "money": "Money"}
        out = [{"id": k, "label": labels[k], "items": items, "total": total}
               for k, (items, total) in groups.items() if total]
        return {"q": q, "terms": terms, "groups": out, "total": sum(g["total"] for g in out),
                "ms": round((time.time() - t0) * 1000)}

    class CaptureIn(BaseModel):
        title: str = Field("", max_length=200)
        body: str = Field("", max_length=200_000)
        folder: str = Field(INBOX, max_length=500)

    @r.post("/notes/capture")
    def capture(body: CaptureIn):
        """A new note in PersonalDocs (default Inbox/). Never overwrites: a name clash gets ' (2)'."""
        title = body.title.strip() or (body.body.strip().splitlines() or [""])[0][:80]
        if not (title or body.body.strip()):
            raise HTTPException(400, "Write something first")
        now = datetime.now().astimezone()
        text = f"---\ncreated: {now.isoformat(timespec='minutes')}\nsource: dashboard\n---\n\n{body.body.strip() or title}\n"
        rel = write_new(body.folder.strip().strip("/") or INBOX, title, text)
        return {"root": VAULT, "path": rel, "href": f"#/notes/{VAULT}?p={quote(rel)}"}

    @r.get("/notes-folders")
    def vault_folders():
        """Folders of the vault (for the capture / export folder picker), most recently changed first."""
        dirs: dict[str, float] = {}
        for f in walk(VAULT):
            parts = f["path"].split("/")[:-1]
            for i in range(1, min(len(parts), 5) + 1):
                d = "/".join(parts[:i])
                dirs[d] = max(dirs.get(d, 0), f["mtime"])
        return {"inbox": INBOX, "folders": [d for d, _ in sorted(dirs.items(), key=lambda x: -x[1])]}

    @r.get("/notes-ws/{slug}")
    def for_workspace(slug: str, limit: int = 8, conn=Depends(get_conn)):
        """The workspace's linked folders as Notes links, and their recently changed notes."""
        W.get_ws(conn, slug)
        folders, recent = [], []
        for f in W.linked_folders(conn, slug):
            loc = locate(f["path"]) if f["ok"] else None
            if not loc:
                continue
            rid, rel = loc
            folders.append({"title": f["title"], "root": rid, "path": rel, "href": f"#/notes/{rid}?p={quote(rel)}"})
            pre = rel + "/" if rel else ""
            recent += [{"root": rid, "path": x["path"], "name": Path(x["path"]).stem, "kind": kind_of(x["path"]),
                        "mtime": x["mtime"], "href": f"#/notes/{rid}?p={quote(x['path'])}"}
                       for x in walk(rid) if x["path"].startswith(pre) and kind_of(x["path"]) in ("md", "pdf")
                       and not x["path"].endswith("/index.md")]
        recent.sort(key=lambda x: -x["mtime"])
        return {"folders": folders, "recent": [{**{k: v for k, v in x.items() if k != "mtime"}, "modified": iso(x["mtime"])}
                                               for x in recent[:max(1, min(limit, 30))]]}

    class ExportIn(BaseModel):
        folder: str | None = Field(None, max_length=500)

    @r.post("/notes-export/{slug}/{name}")
    def export_output(slug: str, name: str, body: ExportIn, conn=Depends(get_conn)):
        """Copy a Studio document (outputs/*.md) into PersonalDocs as a new note."""
        W.get_ws(conn, slug)
        src = (W.ws_dir(slug) / "outputs" / name).resolve()
        if src.parent != (W.ws_dir(slug) / "outputs").resolve() or not src.is_file() or src.suffix.lower() != ".md":
            raise HTTPException(404, "Only Studio documents (Markdown) can be saved as notes")
        folder = body.folder
        if folder is None:                 # default: <the class folder>/Study material, else the Inbox
            locs = [locate(f["path"]) for f in W.linked_folders(conn, slug) if f["ok"]]
            mine = [l[1] for l in locs if l and l[0] == VAULT]
            folder = f"{mine[0]}/Study material" if mine and mine[0] else INBOX
        text = src.read_text(encoding="utf-8")
        title = re.sub(r" \(\d{4}-\d{2}-\d{2} \d{4}\)$", "", src.stem)
        if text.startswith("# "):         # Obsidian shows the file name as the title
            text = text.split("\n", 1)[1].lstrip("\n") if "\n" in text else ""
        ws_name = W.get_ws(conn, slug)["name"]
        front = (f"---\ncreated: {datetime.now().astimezone().isoformat(timespec='minutes')}\nsource: dashboard studio\n"
                 f"workspace: {ws_name}\n---\n\n")
        rel = write_new(folder.strip("/") or INBOX, title, front + text)
        return {"root": VAULT, "path": rel, "href": f"#/notes/{VAULT}?p={quote(rel)}"}

    return r
