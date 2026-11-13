"""Workspaces: NotebookLM-style study spaces with a Claude chat, run by Claude Code on this PC.

Each workspace is a folder:
    workspaces/<slug>/CLAUDE.md   rules Claude follows in this workspace
    workspaces/<slug>/sources/    material you add (Claude only reads it)
    workspaces/<slug>/outputs/    notes / study guides Claude writes
    workspaces/<slug>/history/    one summary per past day (<YYYY-MM-DD>.md)

Linked folders (ws_folders): a folder inside one of LINK_ROOTS (your local repo clones) that
Claude reads live (via --add-dir) every chat / Studio run, so it always sees the current
files, committed or not. Read-only: writing stays limited to outputs/ and history/.

Chat runs `claude -p` (headless Claude Code, your own login) inside the workspace folder,
streaming its output to the browser. One Claude Code session per workspace per day; when a
new day starts, earlier days get summarized into history/ in the background.

Claude's permissions (see CLAUDE_ARGS): read anything in the workspace folder, write only in
outputs/ and history/, use the read-only personal-data MCP tools. No shell, no web.
Each chat run also gets the tasks linked to the workspace (appended to the system prompt);
Claude can only *suggest* tasks (a ```tasks block), which the user approves in the dashboard.
"""
import base64
import os
import html
import json
import re
import shutil
import subprocess
import threading
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

import config
import db
import tasks

HERE = Path(__file__).resolve().parent
WS_ROOT = HERE / "workspaces"
CLAUDE = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude.exe")
GH = shutil.which("gh") or r"C:\Program Files\GitHub CLI\gh.exe"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MAX_UPLOAD = 50 * 1024 * 1024
SOURCE_EXTS = {".pdf", ".md", ".markdown", ".txt", ".csv", ".json", ".png", ".jpg", ".jpeg", ".gif",
               ".webp", ".tex", ".typ", ".bib", ".html", ".htm", ".py", ".c", ".h", ".cpp", ".java",
               ".js", ".ts", ".lua", ".ipynb"}

# Folders on this PC a workspace may link (and everything under them). Only these: a linked
# folder is readable by Claude, so keep this to your own document repos.
LINK_ROOTS = config.paths("link_roots")     # local clones of the docs repos (config.json)
SKIP_DIRS = {".git", ".obsidian", ".trash", ".vscode", "node_modules", "__pycache__", ".venv"}

ALLOWED_TOOLS = ",".join([
    "Read", "Glob", "Grep",
    "Write(./outputs/**)", "Edit(./outputs/**)",
    "Write(./history/**)", "Edit(./history/**)",
    "mcp__personal-data",
])
DISALLOWED_TOOLS = "Bash,PowerShell,WebFetch,WebSearch,NotebookEdit"
CLAUDE_ARGS = ["--permission-mode", "dontAsk", "--allowedTools", ALLOWED_TOOLS,
               "--disallowedTools", DISALLOWED_TOOLS, "--permission-prompts", "none"]

# Claude Code model aliases. Sonnet is the default: much lighter on the plan's usage limits than Opus.
MODELS = {"sonnet": "Sonnet", "opus": "Opus", "haiku": "Haiku"}
DEFAULT_MODEL = "sonnet"
SUMMARY_MODEL = "haiku"          # end-of-day summaries are simple; keep them cheap


def ws_model(conn, slug: str) -> str:
    row = conn.execute("SELECT model FROM workspaces WHERE slug = ?", (slug,)).fetchone()
    return row["model"] if row and row["model"] in MODELS else DEFAULT_MODEL


_locks: dict[str, threading.Lock] = {}


def lock_for(slug: str) -> threading.Lock:
    return _locks.setdefault(slug, threading.Lock())


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def get_conn():
    conn = db.connect(check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------- workspace files

CLAUDE_MD = """# Workspace: {name}

{description}

You are a study assistant inside this workspace, talking with its owner through their
personal dashboard. Work like a careful tutor.

## Sources
- The material is in `sources/` (PDFs, Markdown, text, images, code) AND in any linked
  folders listed in your system prompt under "Linked folders" (the user's own notes and
  reports on their PC, often the main material). Look in all of them before answering.
- Glob and Grep without a `path` only search this workspace folder. To search a linked
  folder, pass its absolute path as `path`. An empty `sources/` does NOT mean there is no
  material: check the linked folders.
- Ground answers in the sources and cite them inline, e.g. *(lecture3.pdf, p. 12)* or
  *(notes.md, "Gradient descent")*. If the sources don't cover something, say so, then
  answer from general knowledge and mark that part clearly.
- Never change, move or delete anything in `sources/`.

## Your files
- You may write only in `outputs/` (notes, study guides, summaries, glossaries; Markdown)
  and `history/`. Use clear file names, e.g. `outputs/study-guide-chapter-3.md`.
- `history/` holds one summary per earlier day (`YYYY-MM-DD.md`). When the user refers to
  an earlier session, read the relevant summary.

## Style
- Reply in the language the user writes in. Use Markdown: short paragraphs, lists, and
  `$...$` / `$$...$$` for math.
- The user's own activity data (sleep, steps, PC use, reports) is available through the
  `personal-data` tools if they ask about it.
"""


def ws_dir(slug: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,60}", slug):
        raise HTTPException(404, "No such workspace")
    return WS_ROOT / slug


def ensure_layout(slug: str, name: str, description: str) -> Path:
    d = ws_dir(slug)
    for sub in ("sources", "outputs", "history"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    (d / "CLAUDE.md").write_text(CLAUDE_MD.format(name=name, description=description or ""), encoding="utf-8")
    return d


def make_slug(conn, name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "workspace"
    slug, n = base, 2
    while conn.execute("SELECT 1 FROM workspaces WHERE slug = ?", (slug,)).fetchone() or (WS_ROOT / slug).exists():
        slug, n = f"{base}-{n}", n + 1
    return slug


def safe_filename(folder: Path, name: str) -> str:
    """A filename that is safe on Windows and not already used in `folder`."""
    name = Path(name).name
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", Path(name).stem).strip(" .") or "source"
    ext = Path(name).suffix.lower()
    candidate, n = f"{stem[:80]}{ext}", 2
    while (folder / candidate).exists():
        candidate, n = f"{stem[:80]} ({n}){ext}", n + 1
    return candidate


def get_ws(conn, slug: str):
    row = conn.execute("SELECT * FROM workspaces WHERE slug = ?", (slug,)).fetchone()
    if not row:
        raise HTTPException(404, "No such workspace")
    return row


def add_source(conn, slug: str, kind: str, title: str, filename: str, origin: str | None) -> dict:
    size = (ws_dir(slug) / "sources" / filename).stat().st_size
    cur = conn.execute(
        "INSERT INTO ws_sources (ws, kind, title, filename, origin, size, added_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (slug, kind, title, filename, origin, size, now_iso()))
    conn.commit()
    return {"id": cur.lastrowid, "kind": kind, "title": title, "filename": filename, "origin": origin, "size": size}


# ---------------------------------------------------------------- fetching sources

class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "pre"}

    def __init__(self):
        super().__init__()
        self.out, self.skip, self.title, self._in_title = [], 0, "", False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK:
            self.out.append("\n")
        if tag in ("h1", "h2", "h3"):
            self.out.append("#" * int(tag[1]) + " ")
        if tag == "li":
            self.out.append("- ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip:
            self.out.append(data)


def page_to_markdown(url: str) -> tuple[str, str]:
    r = httpx.get(url, follow_redirects=True, timeout=30,
                  headers={"User-Agent": "Mozilla/5.0 (personal dashboard; saving a page for study)"})
    r.raise_for_status()
    ctype = r.headers.get("content-type", "")
    if "pdf" in ctype:
        return "", ""                       # caller saves PDFs as files instead
    p = _TextExtractor()
    p.feed(r.text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", html.unescape("".join(p.out))).strip()
    title = " ".join(p.title.split()) or urlparse(url).netloc
    return title, text


def youtube_id(url: str) -> str | None:
    u = urlparse(url)
    host = u.netloc.lower().removeprefix("www.").removeprefix("m.")
    if host == "youtu.be":
        return u.path.strip("/").split("/")[0] or None
    if host.endswith("youtube.com"):
        if u.path == "/watch":
            return parse_qs(u.query).get("v", [None])[0]
        m = re.match(r"/(shorts|embed|live)/([\w-]+)", u.path)
        return m.group(2) if m else None
    return None


def youtube_transcript(video_id: str) -> tuple[str, str]:
    from youtube_transcript_api import YouTubeTranscriptApi
    api = YouTubeTranscriptApi()
    # Captions in the language actually spoken in the video. YouTube's auto-generated track
    # is always in the spoken language, so it tells us which one that is; human-made
    # captions in that language are preferred over it. (Videos often also carry many
    # human translations, e.g. Arabic first, which must not win.)
    transcripts = list(api.list(video_id))
    generated = next((t for t in transcripts if t.is_generated), None)
    if generated:
        spoken = generated.language_code.split("-")[0]
        chosen = next((t for t in transcripts if not t.is_generated
                       and t.language_code.split("-")[0] == spoken), generated)
    else:   # no auto captions: take a human track in a language the user reads, else the first
        by_lang = {t.language_code.split("-")[0]: t for t in transcripts}
        chosen = next((by_lang[l] for l in ("en", "ja", "th") if l in by_lang), transcripts[0])
    fetched = chosen.fetch()
    lines = []
    for s in fetched:
        m, sec = divmod(int(s.start), 60)
        lines.append(f"[{m}:{sec:02d}] {s.text}")
    title = video_id
    try:   # best-effort title via oEmbed (no API key needed)
        o = httpx.get("https://www.youtube.com/oembed", params={"url": f"https://www.youtube.com/watch?v={video_id}",
                      "format": "json"}, timeout=10)
        title = o.json().get("title", video_id)
    except Exception:
        pass
    return title, "\n".join(lines)


def gh_bytes(path: str, accept: str) -> bytes:
    out = subprocess.run([GH, "api", "-H", f"Accept: {accept}", path], capture_output=True, timeout=120,
                         creationflags=NO_WINDOW)
    if out.returncode:
        raise HTTPException(400, f"GitHub: {out.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return out.stdout


def gh_json(path: str):
    return json.loads(gh_bytes(path, "application/vnd.github+json"))


def known_repo(conn, repo: str) -> str:
    row = conn.execute("SELECT repo FROM github_repos WHERE lower(repo) = lower(?)", (repo,)).fetchone()
    if not row:
        raise HTTPException(400, "That repo isn't in your GitHub list (it syncs every 3 hours)")
    return row["repo"]


# ---------------------------------------------------------------- Claude Code runs

def run_claude(prompt: str, cwd: Path, resume: str | None, system: str | None = None, dirs: list[str] = (),
               model: str | None = None):
    """Start headless Claude Code; returns the Popen (stream-json on stdout).
    `system` is appended to the system prompt (linked tasks and folders); `dirs` are the
    linked folders, added with --add-dir so Claude may read them (writing stays denied)."""
    cmd = [CLAUDE, "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages", *CLAUDE_ARGS]
    for d in dirs:
        cmd += ["--add-dir", d]
    if system:
        cmd += ["--append-system-prompt", system]
    if resume:
        cmd += ["--resume", resume]
    if model:
        cmd += ["--model", model]
    proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    proc.stdin.write(prompt)
    proc.stdin.close()
    return proc


# ---------------------------------------------------------------- linked folders

def link_root(path: Path) -> Path | None:
    """The LINK_ROOTS entry that contains `path` (after resolving .. and links), or None."""
    p = path.resolve()
    for root in LINK_ROOTS:
        r = root.resolve()
        if p == r or r in p.parents:
            return r
    return None


def folder_stats(path: Path, limit: int = 5000) -> dict:
    """Files, total size and newest change in a folder (skipping .git etc.)."""
    files = size = 0
    newest = 0.0
    stack = [path]
    while stack and files < limit:
        try:
            entries = list(os.scandir(stack.pop()))
        except OSError:
            continue
        for e in entries:
            if e.is_dir(follow_symlinks=False):
                if e.name not in SKIP_DIRS:
                    stack.append(Path(e.path))
            elif e.is_file(follow_symlinks=False):
                st = e.stat()
                files, size, newest = files + 1, size + st.st_size, max(newest, st.st_mtime)
    return {"files": files, "size": size, "truncated": files >= limit,
            "modified": datetime.fromtimestamp(newest).astimezone().isoformat(timespec="minutes") if newest else None}


def linked_folders(conn, slug: str) -> list[dict]:
    """The workspace's linked folders that still exist and are still inside LINK_ROOTS."""
    out = []
    for f in conn.execute("SELECT * FROM ws_folders WHERE ws = ? ORDER BY added_at", (slug,)):
        p = Path(f["path"])
        out.append({**dict(f), "ok": p.is_dir() and link_root(p) is not None})
    return out


def folders_context(folders: list[dict]) -> str:
    ok = [f for f in folders if f["ok"]]
    if not ok:
        return ""
    lines = "\n".join(f"- {f['path']}  ({f['title']})" for f in ok)
    return ("# Linked folders (live, read-only)\n\nBesides sources/, these folders on the user's PC are linked to this "
            "workspace. They are the user's own notes and work in progress (lecture notes, reports), often the main "
            f"material of this workspace:\n{lines}\n\nAlways look here, not only in sources/. Glob and Grep search only "
            "the workspace folder unless you pass `path`, so pass the linked folder's absolute path as `path` (and Read "
            "files by absolute path). When unsure what's there, Glob `**/*` with that path first. They always show "
            "the current saved state, so re-read files when the user says they changed something. Ignore .git and "
            "editor folders. You can NOT change these folders: never try to write or edit files there. When you "
            "suggest changes to the user's files, show them in the chat (a short diff or the replacement text) or "
            "write them to outputs/, and the user applies them.")


def describe_tool(name: str, inp: dict) -> str:
    path = inp.get("file_path") or inp.get("path") or inp.get("pattern") or ""
    short = Path(path).name if path else ""
    where = f" in {Path(inp['path']).name}" if inp.get("path") and name in ("Glob", "Grep") else ""
    return {"Read": f"Reading {short}", "Write": f"Writing {short}", "Edit": f"Editing {short}",
            "Glob": f"Looking for files {inp.get('pattern', '')}{where}", "Grep": f"Searching for “{inp.get('pattern', '')}”{where}",
            }.get(name, name.replace("mcp__personal-data__", "Looking up your data: "))


def summarize_day(slug: str, day: str) -> None:
    """Background: ask that day's session to write history/<day>.md."""
    conn = db.connect(check_same_thread=False)
    try:
        row = conn.execute("SELECT session_id FROM ws_days WHERE ws = ? AND day = ? AND summarized = 0",
                           (slug, day)).fetchone()
        if not row or not row["session_id"]:
            return
        prompt = (f"The day is over. Write a concise Markdown summary of this session to history/{day}.md: "
                  "topics covered, key explanations and results, which sources were used, files written to "
                  "outputs/, and open questions to continue with. Then reply with just: done")
        proc = run_claude(prompt, ws_dir(slug), row["session_id"], model=SUMMARY_MODEL)
        proc.communicate(timeout=600)
        if (ws_dir(slug) / "history" / f"{day}.md").exists():
            conn.execute("UPDATE ws_days SET summarized = 1 WHERE ws = ? AND day = ?", (slug, day))
            conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------- API

def build_router(check_token) -> APIRouter:
    r = APIRouter(prefix="/workspaces", dependencies=[Depends(check_token)])

    class NewWorkspace(BaseModel):
        name: str = Field(min_length=1, max_length=80)
        description: str = Field("", max_length=1000)

    @r.get("")
    def list_workspaces(conn=Depends(get_conn)):
        out = []
        for w in conn.execute("SELECT * FROM workspaces ORDER BY created_at DESC"):
            stats = conn.execute(
                "SELECT (SELECT COUNT(*) FROM ws_sources WHERE ws = :s) AS sources, "
                "(SELECT COUNT(*) FROM ws_messages WHERE ws = :s AND role = 'user') AS messages, "
                "(SELECT MAX(created_at) FROM ws_messages WHERE ws = :s) AS last_chat", {"s": w["slug"]}).fetchone()
            outputs = len(list((ws_dir(w["slug"]) / "outputs").glob("*"))) if (ws_dir(w["slug"]) / "outputs").exists() else 0
            out.append({**dict(w), **dict(stats), "outputs": outputs})
        return out

    @r.post("")
    def create_workspace(body: NewWorkspace, conn=Depends(get_conn)):
        slug = make_slug(conn, body.name)
        ensure_layout(slug, body.name.strip(), body.description.strip())
        conn.execute("INSERT INTO workspaces (slug, name, description, created_at) VALUES (?, ?, ?, ?)",
                     (slug, body.name.strip(), body.description.strip(), now_iso()))
        conn.commit()
        return {"slug": slug}

    class WorkspacePatch(BaseModel):
        model: str | None = None

    @r.get("/models")
    def models():
        return {"models": [{"id": k, "label": v} for k, v in MODELS.items()], "default": DEFAULT_MODEL}

    @r.patch("/{slug}")
    def update_workspace(slug: str, body: WorkspacePatch, conn=Depends(get_conn)):
        get_ws(conn, slug)
        if body.model is not None:
            if body.model not in MODELS:
                raise HTTPException(400, "Unknown model")
            conn.execute("UPDATE workspaces SET model = ? WHERE slug = ?", (body.model, slug))
            conn.commit()
        return {"slug": slug, "model": ws_model(conn, slug)}

    @r.get("/{slug}")
    def workspace_detail(slug: str, conn=Depends(get_conn)):
        w = get_ws(conn, slug)
        sources = [dict(s) for s in conn.execute("SELECT * FROM ws_sources WHERE ws = ? ORDER BY added_at DESC", (slug,))]
        days = [dict(d) for d in conn.execute(
            "SELECT m.day, COUNT(*) FILTER (WHERE m.role = 'user') AS messages, COALESCE(d.summarized, 0) AS summarized "
            "FROM ws_messages m LEFT JOIN ws_days d ON d.ws = m.ws AND d.day = m.day WHERE m.ws = ? "
            "GROUP BY m.day ORDER BY m.day DESC", (slug,))]
        out_dir = ws_dir(slug) / "outputs"
        outputs = sorted(({"name": p.name, "size": p.stat().st_size,
                           "modified": datetime.fromtimestamp(p.stat().st_mtime).astimezone().isoformat(timespec="minutes")}
                          for p in out_dir.glob("*") if p.is_file()), key=lambda o: o["modified"], reverse=True) if out_dir.exists() else []
        folders = [{**f, **(folder_stats(Path(f["path"])) if f["ok"] else {})} for f in linked_folders(conn, slug)]
        return {**dict(w), "model": ws_model(conn, slug), "sources": sources, "folders": folders, "days": days, "outputs": outputs, "today": date.today().isoformat(),
                "busy": lock_for(slug).locked()}

    @r.delete("/{slug}")
    def delete_workspace(slug: str, conn=Depends(get_conn)):
        get_ws(conn, slug)
        if lock_for(slug).locked():
            raise HTTPException(409, "Claude is still answering in this workspace")
        shutil.rmtree(ws_dir(slug), ignore_errors=True)
        conn.execute("UPDATE tasks SET workspace = NULL WHERE workspace = ?", (slug,))   # tasks stay, unlinked
        conn.execute("UPDATE focus_sessions SET workspace = NULL WHERE workspace = ?", (slug,))
        conn.execute("UPDATE exams SET workspace = NULL WHERE workspace = ?", (slug,))
        for t in ("ws_sources", "ws_folders", "ws_days", "ws_messages"):
            conn.execute(f"DELETE FROM {t} WHERE ws = ?", (slug,))
        conn.execute("DELETE FROM workspaces WHERE slug = ?", (slug,))
        conn.commit()
        return {"deleted": slug}

    @r.get("/{slug}/messages")
    def messages(slug: str, day: str | None = None, conn=Depends(get_conn)):
        get_ws(conn, slug)
        day = day or date.today().isoformat()
        return [{**dict(m), "tools": json.loads(m["tools"])} for m in conn.execute(
            "SELECT id, role, content, tools, cost_usd, created_at FROM ws_messages WHERE ws = ? AND day = ? ORDER BY id",
            (slug, day))]

    # ---- sources
    @r.post("/{slug}/sources/upload")
    async def upload_source(slug: str, file: UploadFile = File(...), conn=Depends(get_conn)):
        get_ws(conn, slug)
        ext = Path(file.filename or "").suffix.lower()
        if ext not in SOURCE_EXTS:
            raise HTTPException(400, f"Unsupported file type {ext or '(none)'}. Use PDF, Markdown, text, images or code.")
        data = await file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "File is larger than 50 MB")
        folder = ws_dir(slug) / "sources"
        name = safe_filename(folder, file.filename)
        (folder / name).write_bytes(data)
        return add_source(conn, slug, "file", Path(file.filename).stem, name, file.filename)

    class TextSource(BaseModel):
        title: str = Field(min_length=1, max_length=120)
        text: str = Field(min_length=1, max_length=2_000_000)

    @r.post("/{slug}/sources/text")
    def text_source(slug: str, body: TextSource, conn=Depends(get_conn)):
        get_ws(conn, slug)
        folder = ws_dir(slug) / "sources"
        name = safe_filename(folder, f"{body.title}.md")
        (folder / name).write_text(f"# {body.title}\n\n{body.text}\n", encoding="utf-8")
        return add_source(conn, slug, "text", body.title, name, None)

    class UrlSource(BaseModel):
        url: str = Field(min_length=8, max_length=2000)

    @r.post("/{slug}/sources/url")
    def url_source(slug: str, body: UrlSource, conn=Depends(get_conn)):
        get_ws(conn, slug)
        url = body.url.strip()
        if urlparse(url).scheme not in ("http", "https"):
            raise HTTPException(400, "Only http(s) links")
        folder = ws_dir(slug) / "sources"
        vid = youtube_id(url)
        try:
            if vid:
                title, text = youtube_transcript(vid)
                name = safe_filename(folder, f"YouTube - {title}.md")
                (folder / name).write_text(f"# {title}\n\nSource: {url}\nTranscript with [minute:second] timestamps.\n\n{text}\n",
                                           encoding="utf-8")
                return add_source(conn, slug, "youtube", title, name, url)
            title, text = page_to_markdown(url)
            if not text:   # a PDF link: save the PDF itself
                data = httpx.get(url, follow_redirects=True, timeout=60).content[:MAX_UPLOAD]
                name = safe_filename(folder, Path(urlparse(url).path).name or "download.pdf")
                (folder / name).write_bytes(data)
                return add_source(conn, slug, "url", Path(name).stem, name, url)
            name = safe_filename(folder, f"{title}.md")
            (folder / name).write_text(f"# {title}\n\nSource: {url}\n\n{text}\n", encoding="utf-8")
            return add_source(conn, slug, "url", title, name, url)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, f"Couldn't get that link: {type(e).__name__}: {str(e)[:200]}")

    @r.get("/{slug}/github/browse")
    def github_browse(slug: str, repo: str, path: str = "", conn=Depends(get_conn)):
        get_ws(conn, slug)
        repo = known_repo(conn, repo)
        items = gh_json(f"repos/{repo}/contents/{quote(path)}")
        if isinstance(items, dict):
            items = [items]
        return sorted(({"name": i["name"], "path": i["path"], "type": i["type"], "size": i.get("size")}
                       for i in items if not i["name"].startswith(".")),
                      key=lambda i: (i["type"] != "dir", i["name"].lower()))

    @r.get("/{slug}/github/releases")
    def github_releases(slug: str, conn=Depends(get_conn)):
        get_ws(conn, slug)
        return [{**dict(x), "assets": json.loads(x["assets"])} for x in conn.execute(
            "SELECT repo, tag, name, published_at, assets FROM docs_releases ORDER BY published_at DESC")]

    class GithubSource(BaseModel):
        repo: str
        path: str | None = None          # a file in the repo ...
        tag: str | None = None           # ... or a release asset (tag + asset name)
        asset: str | None = None

    @r.post("/{slug}/sources/github")
    def github_source(slug: str, body: GithubSource, conn=Depends(get_conn)):
        get_ws(conn, slug)
        repo = known_repo(conn, body.repo)
        folder = ws_dir(slug) / "sources"
        if body.tag and body.asset:
            rel = gh_json(f"repos/{repo}/releases/tags/{quote(body.tag)}")
            asset = next((a for a in rel.get("assets", []) if a["name"] == body.asset), None)
            if not asset:
                raise HTTPException(404, "Release file not found")
            data = gh_bytes(f"repos/{repo}/releases/assets/{asset['id']}", "application/octet-stream")
            name = safe_filename(folder, f"{rel.get('name') or body.tag}{Path(body.asset).suffix}")
            (folder / name).write_bytes(data[:MAX_UPLOAD])
            return add_source(conn, slug, "github", rel.get("name") or body.tag, name, f"{repo} release {body.tag}/{body.asset}")
        if not body.path:
            raise HTTPException(400, "Choose a file or a release")
        data = gh_bytes(f"repos/{repo}/contents/{quote(body.path)}", "application/vnd.github.raw")
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "File is larger than 50 MB")
        name = safe_filename(folder, Path(body.path).name)
        (folder / name).write_bytes(data)
        return add_source(conn, slug, "github", Path(body.path).stem, name, f"{repo}:{body.path}")

    @r.delete("/{slug}/sources/{source_id}")
    def delete_source(slug: str, source_id: int, conn=Depends(get_conn)):
        s = conn.execute("SELECT * FROM ws_sources WHERE ws = ? AND id = ?", (slug, source_id)).fetchone()
        if not s:
            raise HTTPException(404, "No such source")
        (ws_dir(slug) / "sources" / s["filename"]).unlink(missing_ok=True)
        conn.execute("DELETE FROM ws_sources WHERE id = ?", (source_id,))
        conn.commit()
        return {"deleted": source_id}

    # ---- linked folders
    @r.get("/{slug}/folders/roots")
    def folder_roots(slug: str, conn=Depends(get_conn)):
        get_ws(conn, slug)
        return [{"id": i, "name": r.name, "path": str(r)} for i, r in enumerate(LINK_ROOTS) if r.is_dir()]

    @r.get("/{slug}/folders/browse")
    def folder_browse(slug: str, root: int, path: str = "", conn=Depends(get_conn)):
        """Sub-folders of LINK_ROOTS[root]/path, to pick one to link."""
        get_ws(conn, slug)
        if not 0 <= root < len(LINK_ROOTS):
            raise HTTPException(404, "No such folder")
        base = LINK_ROOTS[root].resolve()
        p = (base / path).resolve()
        if link_root(p) != base or not p.is_dir():
            raise HTTPException(404, "No such folder")
        dirs, files = [], 0
        for e in sorted(os.scandir(p), key=lambda e: e.name.lower()):
            if e.is_dir(follow_symlinks=False) and e.name not in SKIP_DIRS and not e.name.startswith("."):
                dirs.append({"name": e.name, "path": Path(e.path).relative_to(base).as_posix()})
            elif e.is_file(follow_symlinks=False):
                files += 1
        rel = p.relative_to(base).as_posix()
        return {"root": base.name, "path": "" if rel == "." else rel, "abs": str(p), "dirs": dirs, "files": files}

    class FolderIn(BaseModel):
        root: int
        path: str = Field("", max_length=1000)

    @r.post("/{slug}/folders")
    def link_folder(slug: str, body: FolderIn, conn=Depends(get_conn)):
        get_ws(conn, slug)
        if not 0 <= body.root < len(LINK_ROOTS):
            raise HTTPException(400, "No such folder")
        base = LINK_ROOTS[body.root].resolve()
        p = (base / body.path).resolve()
        if link_root(p) != base or not p.is_dir():
            raise HTTPException(400, "That folder isn't inside one of the allowed folders")
        if conn.execute("SELECT 1 FROM ws_folders WHERE ws = ? AND path = ?", (slug, str(p))).fetchone():
            raise HTTPException(409, "That folder is already linked")
        title = p.name if p != base else base.name
        cur = conn.execute("INSERT INTO ws_folders (ws, path, title, added_at) VALUES (?, ?, ?, ?)",
                           (slug, str(p), title, now_iso()))
        conn.commit()
        return {"id": cur.lastrowid, "path": str(p), "title": title}

    @r.delete("/{slug}/folders/{folder_id}")
    def unlink_folder(slug: str, folder_id: int, conn=Depends(get_conn)):
        """Unlink only: the folder itself is never touched."""
        get_ws(conn, slug)
        conn.execute("DELETE FROM ws_folders WHERE ws = ? AND id = ?", (slug, folder_id))
        conn.commit()
        return {"unlinked": folder_id}

    @r.get("/{slug}/folders/{folder_id}/files")
    def folder_files(slug: str, folder_id: int, conn=Depends(get_conn)):
        """What Claude sees in a linked folder: files (relative path, size, modified), newest first."""
        f = next((x for x in linked_folders(conn, slug) if x["id"] == folder_id and x["ok"]), None)
        if not f:
            raise HTTPException(404, "No such linked folder")
        base, out, stack = Path(f["path"]), [], [Path(f["path"])]
        while stack and len(out) < 2000:
            for e in os.scandir(stack.pop()):
                if e.is_dir(follow_symlinks=False):
                    if e.name not in SKIP_DIRS:
                        stack.append(Path(e.path))
                elif e.is_file(follow_symlinks=False):
                    st = e.stat()
                    out.append({"path": Path(e.path).relative_to(base).as_posix(), "size": st.st_size,
                                "modified": datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="minutes")})
        return sorted(out, key=lambda x: x["modified"], reverse=True)

    @r.get("/{slug}/files/{area}/{name}")
    def get_file(slug: str, area: str, name: str):
        if area not in ("sources", "outputs", "history"):
            raise HTTPException(404, "Not found")
        folder = (ws_dir(slug) / area).resolve()
        p = (folder / name).resolve()
        if p.parent != folder or not p.is_file():
            raise HTTPException(404, "Not found")
        media = "text/markdown; charset=utf-8" if p.suffix.lower() in (".md", ".markdown") else None
        return FileResponse(p, media_type=media)

    # ---- chat
    class ChatIn(BaseModel):
        message: str = Field(min_length=1, max_length=20000)

    @r.post("/{slug}/chat")
    def chat(slug: str, body: ChatIn, conn=Depends(get_conn)):
        w = get_ws(conn, slug)
        ensure_layout(slug, w["name"], w["description"])       # CLAUDE.md follows the current template
        lock = lock_for(slug)
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "Claude is still answering in this workspace")
        today = date.today().isoformat()
        row = conn.execute("SELECT session_id FROM ws_days WHERE ws = ? AND day = ?", (slug, today)).fetchone()
        resume = row["session_id"] if row else None
        if not resume:   # first message today: summarize earlier days in the background
            for d in conn.execute("SELECT day FROM ws_days WHERE ws = ? AND day < ? AND summarized = 0 "
                                  "AND session_id IS NOT NULL ORDER BY day DESC LIMIT 3", (slug, today)):
                threading.Thread(target=summarize_day, args=(slug, d["day"]), daemon=True).start()
        conn.execute("INSERT INTO ws_messages (ws, day, role, content, created_at) VALUES (?, ?, 'user', ?, ?)",
                     (slug, today, body.message, now_iso()))
        conn.commit()

        def stream():
            c = db.connect(check_same_thread=False)
            text_parts, tools, result = [""], [], None
            try:
                folders = linked_folders(c, slug)
                import focus
                system = "\n\n".join(x for x in (folders_context(folders), tasks.workspace_context(c, slug),
                                                  focus.workspace_context(c, slug)) if x)
                proc = run_claude(body.message, ws_dir(slug), resume, system, [f["path"] for f in folders if f["ok"]],
                                  model=ws_model(c, slug))
                yield sse({"type": "status", "text": "Claude is thinking…"})
                for line in proc.stdout:
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    t = ev.get("type")
                    if t == "stream_event":
                        e = ev.get("event", {})
                        if e.get("type") == "content_block_delta" and e.get("delta", {}).get("type") == "text_delta":
                            text_parts[-1] += e["delta"]["text"]
                            yield sse({"type": "text", "text": e["delta"]["text"]})
                        elif e.get("type") == "message_start" and text_parts[-1]:
                            text_parts.append("")                       # new assistant turn
                            yield sse({"type": "text", "text": "\n\n"})
                    elif t == "assistant":
                        for b in ev.get("message", {}).get("content", []):
                            if b.get("type") == "tool_use":
                                what = describe_tool(b.get("name", ""), b.get("input", {}))
                                tools.append(what)
                                yield sse({"type": "tool", "text": what})
                    elif t == "system" and ev.get("subtype") == "init" and ev.get("session_id"):
                        # store the session id every run: resuming can hand out a new one
                        c.execute("INSERT INTO ws_days (ws, day, session_id) VALUES (?, ?, ?) "
                                  "ON CONFLICT (ws, day) DO UPDATE SET session_id = excluded.session_id",
                                  (slug, today, ev.get("session_id")))
                        c.commit()
                    elif t == "result":
                        result = ev
                proc.wait(timeout=30)
                err = proc.stderr.read()
                answer = "\n\n".join(p.strip() for p in text_parts if p.strip()) or (result or {}).get("result", "")
                if result and not result.get("is_error") and answer:
                    c.execute("INSERT INTO ws_messages (ws, day, role, content, tools, cost_usd, created_at) "
                              "VALUES (?, ?, 'assistant', ?, ?, ?, ?)",
                              (slug, today, answer, json.dumps(tools, ensure_ascii=False),
                               result.get("total_cost_usd"), now_iso()))
                    c.commit()
                    yield sse({"type": "done", "cost_usd": result.get("total_cost_usd")})
                else:
                    msg = (result or {}).get("result") or err.strip()[:500] or "Claude Code stopped without an answer."
                    if re.search(r"login|authenticat|api key|oauth", msg, re.I):
                        msg = f"Claude Code isn't signed in for headless use: {msg}"
                    c.execute("INSERT INTO ws_messages (ws, day, role, content, created_at) VALUES (?, ?, 'error', ?, ?)",
                              (slug, today, msg, now_iso()))
                    c.commit()
                    yield sse({"type": "error", "text": msg})
            except Exception as e:
                yield sse({"type": "error", "text": f"{type(e).__name__}: {e}"})
            finally:
                c.close()
                lock.release()

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @r.post("/{slug}/days/{day}/summary")
    def summarize_now(slug: str, day: str, conn=Depends(get_conn)):
        get_ws(conn, slug)
        threading.Thread(target=summarize_day, args=(slug, day), daemon=True).start()
        return {"started": day}

    return r
