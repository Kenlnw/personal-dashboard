"""Tasks: a small Linear-style issue tracker.

Issues ("tasks") have an id shown as T-<n>, a title, a Markdown description, a status
(backlog → todo → in_progress → done / canceled), a priority (0 none, 1 urgent, 2 high,
3 medium, 4 low — Linear's order), labels, an optional project, a due date and an optional
parent (sub-issues) and an optional linked workspace. Every change is written to
task_activity, together with comments, so each task has a history.

Workspaces: a task can be linked to a workspace. Claude in that workspace (chat and Studio)
gets the linked tasks as context (`workspace_context`) and can *suggest* new tasks in a
```tasks JSON block or a Study plan; the user approves them, which calls POST /tasks/batch.
`origin` remembers which suggestion a task came from, so approving twice adds nothing.

Recurring tasks (task_recurrences): a schedule (weekdays, start, optional end). Each
scheduled day gets ONE task, created on that day with due = that day and origin
rec:<id>:<date> (so it is never created twice). A background thread runs the generator
every 10 minutes (and GET /tasks runs it too). When `close_missed` is on, a day's task that
is still open after the day is over is canceled ("missed"), so missed days don't pile up.

`sort` is a float used for manual order inside a status column (drag and drop puts a task
between two neighbours by taking the midpoint).

The router uses the same auth as the rest of the API: Tailscale identity (dashboard) or
the API token (iPhone Shortcuts: POST /tasks {"title": "..."}).
"""
import json
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import db

STATUSES = ["backlog", "todo", "in_progress", "done", "canceled"]
CLOSED = {"done", "canceled"}
PRIORITIES = {0: "No priority", 1: "Urgent", 2: "High", 3: "Medium", 4: "Low"}
STATUS_LABEL = {"backlog": "Backlog", "todo": "Todo", "in_progress": "In Progress", "done": "Done", "canceled": "Canceled"}
LABEL_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#9b59d0", "#d03b3b", "#d4a21a", "#3aa6b9", "#898781"]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def get_conn():
    conn = db.connect(check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


def task_row(r) -> dict:
    d = dict(r)
    d["labels"] = json.loads(d["labels"])
    d["key"] = f"T-{d['id']}"
    return d


def get_task(conn, task_id: int) -> dict:
    r = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not r:
        raise HTTPException(404, f"No task T-{task_id}")
    return task_row(r)


def log(conn, task_id: int, kind: str, data: dict | None = None, body: str | None = None):
    conn.execute("INSERT INTO task_activity (task_id, kind, data, body, created_at) VALUES (?, ?, ?, ?, ?)",
                 (task_id, kind, json.dumps(data or {}, ensure_ascii=False), body, now_iso()))


def ensure_labels(conn, names: list[str]) -> list[str]:
    """Clean a label list and create any label that doesn't exist yet (with a color)."""
    out = []
    for n in names:
        n = n.strip()[:40]
        if n and n.lower() not in (o.lower() for o in out):
            row = conn.execute("SELECT name FROM task_labels WHERE name = ? COLLATE NOCASE", (n,)).fetchone()
            if row:
                n = row["name"]
            else:
                count = conn.execute("SELECT COUNT(*) FROM task_labels").fetchone()[0]
                conn.execute("INSERT INTO task_labels (name, color) VALUES (?, ?)", (n, LABEL_COLORS[count % len(LABEL_COLORS)]))
            out.append(n)
    return out


def check_project(conn, project_id):
    if project_id is not None and not conn.execute("SELECT 1 FROM task_projects WHERE id = ?", (project_id,)).fetchone():
        raise HTTPException(400, f"No project {project_id}")


def check_workspace(conn, slug):
    if slug is not None and not conn.execute("SELECT 1 FROM workspaces WHERE slug = ?", (slug,)).fetchone():
        raise HTTPException(400, f"No workspace {slug}")


def check_parent(conn, task_id, parent_id):
    if parent_id is None:
        return
    if parent_id == task_id:
        raise HTTPException(400, "A task can't be its own parent")
    p = parent_id
    while p is not None:                       # no cycles: walk up from the new parent
        row = conn.execute("SELECT parent_id FROM tasks WHERE id = ?", (p,)).fetchone()
        if not row:
            raise HTTPException(400, f"No task T-{parent_id}")
        if task_id is not None and row["parent_id"] == task_id:
            raise HTTPException(400, "That would make a loop of sub-issues")
        p = row["parent_id"]


def top_sort(conn, status: str) -> float:
    """A sort value that puts a task at the top of its status column."""
    row = conn.execute("SELECT MIN(sort) FROM tasks WHERE status = ?", (status,)).fetchone()
    return (row[0] if row[0] is not None else 0) - 1


# ---------------------------------------------------------------- recurring tasks

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def rec_days(rec, until: date):
    """Scheduled days of a recurrence from its start up to `until` (inclusive)."""
    wd = set(json.loads(rec["weekdays"]))
    d, last = date.fromisoformat(rec["start"]), min(until, date.fromisoformat(rec["end"])) if rec["end"] else until
    while d <= last:
        if d.weekday() in wd:
            yield d
        d += timedelta(days=1)


def generate_recurring(conn, today: date | None = None) -> int:
    """Create today's task for each active recurrence, and close missed days. Idempotent."""
    today = today or date.today()
    made = 0
    for rec in conn.execute("SELECT * FROM task_recurrences WHERE paused = 0").fetchall():
        if rec["close_missed"]:
            for t in conn.execute("SELECT id, status FROM tasks WHERE origin LIKE ? AND due < ? AND status NOT IN ('done','canceled')",
                                  (f"rec:{rec['id']}:%", today.isoformat())).fetchall():
                ts = now_iso()
                conn.execute("UPDATE tasks SET status = 'canceled', completed_at = ?, updated_at = ? WHERE id = ?", (ts, ts, t["id"]))
                log(conn, t["id"], "missed", {"from": t["status"]})
        if today.weekday() not in json.loads(rec["weekdays"]) or rec["start"] > today.isoformat() \
                or (rec["end"] and rec["end"] < today.isoformat()):
            continue
        origin = f"rec:{rec['id']}:{today.isoformat()}"
        if conn.execute("SELECT 1 FROM tasks WHERE origin = ?", (origin,)).fetchone():
            continue                     # (the unique index on origin also stops a parallel run)
        desc = rec["description"]
        if rec["link"]:
            desc = f"{desc}\n\n[{rec['link_label'] or 'Open'} →]({rec['link']})".strip()
        ts = now_iso()
        try:
            cur = conn.execute(
                "INSERT INTO tasks (title, description, status, priority, labels, project_id, parent_id, due, sort, "
                "workspace, origin, created_at, updated_at) VALUES (?, ?, 'todo', ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)",
                (rec["title"], desc, rec["priority"], rec["labels"], rec["project_id"], today.isoformat(),
                 top_sort(conn, "todo"), rec["workspace"], origin, ts, ts))
        except sqlite3.IntegrityError:
            continue                     # another request created it a moment ago
        log(conn, cur.lastrowid, "created", {"origin": origin})
        made += 1
    conn.commit()
    return made


def rec_stats(conn, rec, today: date | None = None) -> dict:
    """Progress so far: scheduled days, done, missed, current streak, next day."""
    today = today or date.today()
    days = list(rec_days(rec, today))
    status = {r["due"]: r["status"] for r in conn.execute(
        "SELECT due, status FROM tasks WHERE origin LIKE ?", (f"rec:{rec['id']}:%",))}
    done = sum(1 for d in days if status.get(d.isoformat()) == "done")
    streak = 0
    for d in reversed(days):                 # today doesn't break the streak until it's over
        s = status.get(d.isoformat())
        if s == "done":
            streak += 1
        elif d == today:
            continue
        else:
            break
    total = len(list(rec_days(rec, date.fromisoformat(rec["end"])))) if rec["end"] else None
    nxt = next((d for d in rec_days(rec, today + timedelta(days=14)) if d > today), None)
    ended = bool(rec["end"]) and rec["end"] < today.isoformat()
    return {"scheduled_so_far": len(days), "done": done, "missed": sum(1 for d in days if d < today and status.get(d.isoformat()) != "done"),
            "streak": streak, "total": total, "today": status.get(today.isoformat()) if days and days[-1] == today else None,
            "next": nxt.isoformat() if nxt else None, "ended": ended}


def rec_row(conn, r) -> dict:
    d = dict(r)
    d["labels"], d["weekdays"] = json.loads(d["labels"]), json.loads(d["weekdays"])
    d["paused"], d["close_missed"] = bool(d["paused"]), bool(d["close_missed"])
    return {**d, "stats": rec_stats(conn, r)}


def start_generator() -> None:
    """Called by the API server at startup only (the read-only MCP server also imports main)."""
    threading.Thread(target=_generator_loop, daemon=True, name="recurring-tasks").start()


def _generator_loop():
    while True:
        try:
            conn = db.connect(check_same_thread=False)
            try:
                generate_recurring(conn)
            finally:
                conn.close()
        except Exception:
            pass
        time.sleep(600)


def summary(conn) -> dict:
    """Counts for the overview / MCP: open by status, due today, overdue, done this week."""
    today = date.today().isoformat()
    week = (date.today() - timedelta(days=6)).isoformat()
    by_status = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status")}
    q = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731
    return {
        "open": sum(by_status.get(s, 0) for s in ("backlog", "todo", "in_progress")),
        "by_status": by_status,
        "due_today": q("SELECT COUNT(*) FROM tasks WHERE status NOT IN ('done','canceled') AND due = ?", today),
        "overdue": q("SELECT COUNT(*) FROM tasks WHERE status NOT IN ('done','canceled') AND due < ?", today),
        "done_last_7_days": q("SELECT COUNT(*) FROM tasks WHERE status = 'done' AND substr(completed_at,1,10) >= ?", week),
    }


FENCE = "`" * 3
SUGGEST_RULES = (
    "## Suggesting tasks\n"
    "When the user asks for tasks, to-dos, a plan or a schedule (or agrees to one you offered), put the proposed "
    "tasks in ONE fenced code block whose language tag is `tasks`, containing a JSON array. The dashboard shows "
    "it as a checklist and the user picks which ones to add. Format:\n"
    f"{FENCE}tasks\n"
    '[{"title": "Read chapter 3 (pp. 40-62)", "description": "Short Markdown; cite the source", "priority": 3, '
    '"due": "YYYY-MM-DD", "labels": ["reading"], "subtasks": [{"title": "Summarize section 3.2", "due": null}]}]\n'
    f"{FENCE}\n"
    "Rules: strict JSON (double quotes, no comments); `priority` 0 none, 1 urgent, 2 high, 3 medium, 4 low; "
    "`due` is a date on or after today, or null; titles are concrete actions that start with a verb; don't repeat "
    "existing tasks; at most 12 top-level tasks, each with at most 8 subtasks. Explain the plan briefly outside "
    "the block. Never write tasks into files."
)


def task_lines(conn, slug: str) -> str:
    """Today's date plus the open (and recently done) tasks linked to a workspace, as Markdown."""
    today = date.today()
    pname = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM task_projects")}

    def line(t):
        bits = [STATUS_LABEL[t["status"]]]
        if t["priority"]:
            bits.append(PRIORITIES[t["priority"]])
        if t["due"]:
            bits.append(f"due {t['due']}")
        if t["project_id"] in pname:
            bits.append(f"project {pname[t['project_id']]}")
        labels = json.loads(t["labels"])
        if labels:
            bits.append("labels " + ", ".join(labels))
        sub = f" (sub-issue of T-{t['parent_id']})" if t["parent_id"] else ""
        return f"- T-{t['id']} [{'; '.join(bits)}] {t['title']}{sub}"

    open_ = conn.execute("SELECT * FROM tasks WHERE workspace = ? AND status NOT IN ('done','canceled') "
                         "ORDER BY COALESCE(due, '9999'), CASE priority WHEN 0 THEN 9 ELSE priority END LIMIT 60",
                         (slug,)).fetchall()
    since = (datetime.now().astimezone() - timedelta(days=14)).isoformat(timespec="seconds")
    done = conn.execute("SELECT * FROM tasks WHERE workspace = ? AND status = 'done' AND completed_at >= ? "
                        "ORDER BY completed_at DESC LIMIT 15", (slug, since)).fetchall()
    parts = [f"Today is {today.isoformat()} ({today:%A}).",
             "Open tasks linked to this workspace:\n" + ("\n".join(map(line, open_)) if open_ else "(none)")]
    if done:
        parts.append("Completed in the last 14 days:\n" +
                     "\n".join(f"- T-{t['id']} {t['title']} (done {t['completed_at'][:10]})" for t in done))
    return "\n\n".join(parts)


def workspace_context(conn, slug: str) -> str:
    """Appended to Claude's system prompt in a workspace chat: the linked tasks + how to suggest new ones."""
    return ("# Tasks (the user's task tracker on their dashboard)\n\n" + task_lines(conn, slug) +
            "\n\nUse these when they matter (what to work on first, deadlines, progress). "
            "You cannot change tasks yourself.\n\n" + SUGGEST_RULES)


# ---------------------------------------------------------------- API

class NewTask(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field("", max_length=100_000)
    status: str = "todo"
    priority: int = Field(0, ge=0, le=4)
    labels: list[str] = []
    project_id: int | None = None
    parent_id: int | None = None
    due: date | None = None
    workspace: str | None = None
    origin: str | None = Field(None, max_length=200)


class SubSuggestion(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field("", max_length=20_000)
    priority: int = Field(0, ge=0, le=4)
    due: date | None = None
    labels: list[str] = []
    origin: str | None = Field(None, max_length=200)


class Suggestion(SubSuggestion):
    subtasks: list[SubSuggestion] = Field([], max_length=30)


class Batch(BaseModel):
    """Approved suggestions: each becomes a task (its subtasks become sub-issues)."""
    tasks: list[Suggestion] = Field(min_length=1, max_length=60)
    status: str = "todo"
    workspace: str | None = None
    project_id: int | None = None


class TaskPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    description: str | None = Field(None, max_length=100_000)
    status: str | None = None
    priority: int | None = Field(None, ge=0, le=4)
    labels: list[str] | None = None
    project_id: int | None = None
    parent_id: int | None = None
    due: date | None = None
    workspace: str | None = None
    sort: float | None = None


class NewProject(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    color: str = Field("#2a78d6", pattern=r"^#[0-9a-fA-F]{6}$")
    description: str = Field("", max_length=1000)


class ProjectPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    color: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    description: str | None = Field(None, max_length=1000)
    archived: bool | None = None


class Recurrence(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field("", max_length=20_000)
    priority: int = Field(0, ge=0, le=4)
    labels: list[str] = []
    project_id: int | None = None
    workspace: str | None = None
    link: str | None = Field(None, max_length=1000, pattern=r"^#/")      # dashboard links only
    link_label: str | None = Field(None, max_length=60)
    weekdays: list[int] = Field([0, 1, 2, 3, 4, 5, 6], min_length=1, max_length=7)
    start: date
    end: date | None = None
    close_missed: bool = True


class RecurrencePatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    description: str | None = Field(None, max_length=20_000)
    priority: int | None = Field(None, ge=0, le=4)
    weekdays: list[int] | None = Field(None, min_length=1, max_length=7)
    end: date | None = None
    close_missed: bool | None = None
    paused: bool | None = None


class Comment(BaseModel):
    body: str = Field(min_length=1, max_length=20_000)


def build_router(check_token) -> APIRouter:
    r = APIRouter(prefix="/tasks", tags=["tasks"], dependencies=[Depends(check_token)])

    @r.get("")
    def list_tasks(closed_days: int = 14, workspace: str | None = None, conn=Depends(get_conn)):
        """All open tasks, plus tasks closed in the last `closed_days` days (-1 = every closed task).
        `workspace` = only tasks linked to that workspace. Also returns projects, labels,
        workspaces and the lists of statuses / priorities."""
        generate_recurring(conn)
        where, args = [], []
        if closed_days >= 0:
            where.append("(status NOT IN ('done','canceled') OR completed_at >= ?)")
            args.append((datetime.now().astimezone() - timedelta(days=closed_days)).isoformat(timespec="seconds"))
        if workspace:
            where.append("workspace = ?")
            args.append(workspace)
        rows = conn.execute(f"SELECT * FROM tasks {'WHERE ' + ' AND '.join(where) if where else ''} "
                            "ORDER BY sort, id DESC", args)
        tasks = [task_row(t) for t in rows]
        projects = [dict(p) for p in conn.execute(
            "SELECT p.*, (SELECT COUNT(*) FROM tasks t WHERE t.project_id = p.id AND t.status NOT IN ('done','canceled')) AS open, "
            "(SELECT COUNT(*) FROM tasks t WHERE t.project_id = p.id) AS total FROM task_projects p ORDER BY p.archived, p.name")]
        labels = [dict(lb) for lb in conn.execute("SELECT * FROM task_labels ORDER BY name COLLATE NOCASE")]
        closed_total = conn.execute("SELECT COUNT(*) FROM tasks WHERE status IN ('done','canceled')").fetchone()[0]
        workspaces = [dict(w) for w in conn.execute("SELECT slug, name FROM workspaces ORDER BY name COLLATE NOCASE")]
        return {"tasks": tasks, "projects": projects, "labels": labels, "workspaces": workspaces, "closed_total": closed_total,
                "statuses": [{"id": s, "name": STATUS_LABEL[s]} for s in STATUSES],
                "priorities": [{"id": k, "name": v} for k, v in PRIORITIES.items()],
                "today": date.today().isoformat()}

    @r.get("/summary")
    def tasks_summary(conn=Depends(get_conn)):
        return summary(conn)

    # ---- recurring (registered before /{task_id} so "recurring" isn't read as a task id)
    @r.get("/recurring")
    def list_recurring(conn=Depends(get_conn)):
        generate_recurring(conn)
        return [rec_row(conn, x) for x in conn.execute("SELECT * FROM task_recurrences ORDER BY paused, created_at DESC")]

    @r.post("/recurring")
    def create_recurring(body: Recurrence, conn=Depends(get_conn)):
        """A repeating task. Today's task is created right away when today is a scheduled day."""
        if any(not 0 <= d <= 6 for d in body.weekdays):
            raise HTTPException(400, "weekdays are 0 (Monday) … 6 (Sunday)")
        if body.end and body.end < body.start:
            raise HTTPException(400, "The end date is before the start date")
        check_project(conn, body.project_id)
        check_workspace(conn, body.workspace)
        cur = conn.execute(
            "INSERT INTO task_recurrences (title, description, priority, labels, project_id, workspace, link, link_label, "
            "weekdays, start, end, close_missed, paused, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (body.title.strip(), body.description, body.priority, json.dumps(ensure_labels(conn, body.labels), ensure_ascii=False),
             body.project_id, body.workspace, body.link, body.link_label, json.dumps(sorted(set(body.weekdays))),
             body.start.isoformat(), body.end.isoformat() if body.end else None, int(body.close_missed), now_iso()))
        conn.commit()
        generate_recurring(conn)
        return rec_row(conn, conn.execute("SELECT * FROM task_recurrences WHERE id = ?", (cur.lastrowid,)).fetchone())

    @r.patch("/recurring/{rec_id}")
    def update_recurring(rec_id: int, body: RecurrencePatch, conn=Depends(get_conn)):
        if not conn.execute("SELECT 1 FROM task_recurrences WHERE id = ?", (rec_id,)).fetchone():
            raise HTTPException(404, "No such recurring task")
        sets = {}
        for k, v in body.model_dump(exclude_unset=True).items():
            if v is None and k != "end":
                continue
            sets[k] = (json.dumps(sorted(set(v))) if k == "weekdays" else int(v) if isinstance(v, bool)
                       else v.isoformat() if isinstance(v, date) else v)
        if sets:
            conn.execute(f"UPDATE task_recurrences SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", (*sets.values(), rec_id))
            conn.commit()
            generate_recurring(conn)
        return rec_row(conn, conn.execute("SELECT * FROM task_recurrences WHERE id = ?", (rec_id,)).fetchone())

    @r.delete("/recurring/{rec_id}")
    def delete_recurring(rec_id: int, conn=Depends(get_conn)):
        """Stop repeating. Tasks already created stay (open ones too)."""
        conn.execute("DELETE FROM task_recurrences WHERE id = ?", (rec_id,))
        conn.commit()
        return {"deleted": rec_id}

    @r.post("")
    def create_task(body: NewTask, conn=Depends(get_conn)):
        """Create a task. From a Shortcut, `{"title": "Buy milk"}` is enough (status Todo)."""
        if body.status not in STATUSES:
            raise HTTPException(400, f"status must be one of {STATUSES}")
        check_project(conn, body.project_id)
        check_parent(conn, None, body.parent_id)
        check_workspace(conn, body.workspace)
        new_id = insert_task(conn, body)
        conn.commit()
        return get_task(conn, new_id)

    def insert_task(conn, body: NewTask) -> int:
        labels = ensure_labels(conn, body.labels)
        ts = now_iso()
        cur = conn.execute(
            "INSERT INTO tasks (title, description, status, priority, labels, project_id, parent_id, due, sort, "
            "workspace, origin, created_at, updated_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (body.title.strip(), body.description, body.status, body.priority, json.dumps(labels, ensure_ascii=False),
             body.project_id, body.parent_id, body.due.isoformat() if body.due else None, top_sort(conn, body.status),
             body.workspace, body.origin, ts, ts, ts if body.status in CLOSED else None))
        log(conn, cur.lastrowid, "created", {"origin": body.origin} if body.origin else None)
        return cur.lastrowid

    @r.post("/batch")
    def create_batch(body: Batch, conn=Depends(get_conn)):
        """Add approved suggestions (from a workspace chat or a Study plan). A suggestion whose
        `origin` was already added is skipped, so pressing Add twice creates nothing new."""
        if body.status not in STATUSES:
            raise HTTPException(400, f"status must be one of {STATUSES}")
        check_project(conn, body.project_id)
        check_workspace(conn, body.workspace)

        def existing(origin):
            return origin and conn.execute("SELECT id FROM tasks WHERE origin = ?", (origin,)).fetchone()

        created, skipped = [], 0                   # in reading order: task, its subtasks, next task …
        for sug in body.tasks:
            row = existing(sug.origin)
            if row:
                parent_id, skipped = row["id"], skipped + 1
            else:
                parent_id = insert_task(conn, NewTask(**sug.model_dump(exclude={"subtasks"}), status=body.status,
                                                      workspace=body.workspace, project_id=body.project_id))
                created.append(parent_id)
            for sub in sug.subtasks:
                if existing(sub.origin):
                    skipped += 1
                    continue
                created.append(insert_task(conn, NewTask(**sub.model_dump(), status=body.status, parent_id=parent_id,
                                                         workspace=body.workspace, project_id=body.project_id)))
        if created:                                # put the new tasks on top, in that same order
            top = conn.execute("SELECT MIN(sort) FROM tasks WHERE status = ? AND id NOT IN (%s)"
                               % ",".join("?" * len(created)), (body.status, *created)).fetchone()[0] or 0
            for i, tid in enumerate(created):
                conn.execute("UPDATE tasks SET sort = ? WHERE id = ?", (top - len(created) + i, tid))
        conn.commit()
        return {"created": [get_task(conn, i) for i in sorted(created)], "skipped": skipped}

    @r.get("/{task_id}")
    def task_detail(task_id: int, conn=Depends(get_conn)):
        t = get_task(conn, task_id)
        t["children"] = [task_row(c) for c in conn.execute("SELECT * FROM tasks WHERE parent_id = ? ORDER BY sort, id", (task_id,))]
        t["parent"] = get_task(conn, t["parent_id"]) if t["parent_id"] else None
        rid = t["origin"].split(":")[1] if (t["origin"] or "").startswith("rec:") else None
        rec = rid and conn.execute("SELECT * FROM task_recurrences WHERE id = ?", (rid,)).fetchone()
        t["recurrence"] = {"id": rec["id"], "title": rec["title"], "stats": rec_stats(conn, rec)} if rec else None
        n, mins = conn.execute("SELECT COUNT(*), COALESCE(SUM(minutes), 0) FROM focus_sessions "
                               "WHERE task_id = ? AND ended_at IS NOT NULL", (task_id,)).fetchone()
        t["focus"] = {"sessions": n, "minutes": round(mins, 1)}
        t["activity"] = [{**dict(a), "data": json.loads(a["data"])} for a in conn.execute(
            "SELECT * FROM task_activity WHERE task_id = ? ORDER BY id", (task_id,))]
        return t

    @r.patch("/{task_id}")
    def update_task(task_id: int, body: TaskPatch, conn=Depends(get_conn)):
        old = get_task(conn, task_id)
        changes = body.model_dump(exclude_unset=True)
        sets: dict = {}
        for field, value in changes.items():
            if value is None and field in ("title", "status", "priority", "sort"):
                continue                              # these can't be cleared
            if field == "status":
                if value not in STATUSES:
                    raise HTTPException(400, f"status must be one of {STATUSES}")
                if value != old["status"]:
                    sets["completed_at"] = now_iso() if value in CLOSED else None
                    if "sort" not in changes:
                        sets["sort"] = top_sort(conn, value)
            elif field == "project_id":
                check_project(conn, value)
            elif field == "parent_id":
                check_parent(conn, task_id, value)
            elif field == "workspace":
                check_workspace(conn, value)
            elif field == "labels":
                value = json.dumps(ensure_labels(conn, value or []), ensure_ascii=False)
            elif field == "due":
                value = value.isoformat() if value else None
            elif field == "title":
                value = value.strip()
            sets[field] = value
        if not sets:
            return old
        sets["updated_at"] = now_iso()
        conn.execute(f"UPDATE tasks SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", (*sets.values(), task_id))
        new = get_task(conn, task_id)
        for field in ("title", "status", "priority", "labels", "project_id", "parent_id", "due", "workspace"):
            if field in changes and old[field] != new[field]:
                log(conn, task_id, field, {"from": old[field], "to": new[field]})
        if "description" in changes and old["description"] != new["description"]:
            log(conn, task_id, "description")
        conn.commit()
        return new

    @r.delete("/{task_id}")
    def delete_task(task_id: int, conn=Depends(get_conn)):
        get_task(conn, task_id)
        conn.execute("UPDATE tasks SET parent_id = NULL WHERE parent_id = ?", (task_id,))   # sub-issues stay
        conn.execute("DELETE FROM task_activity WHERE task_id = ?", (task_id,))
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()
        return {"deleted": task_id}

    @r.post("/{task_id}/comments")
    def add_comment(task_id: int, body: Comment, conn=Depends(get_conn)):
        get_task(conn, task_id)
        log(conn, task_id, "comment", body=body.body.strip())
        conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now_iso(), task_id))
        conn.commit()
        return {"ok": True}

    @r.delete("/{task_id}/comments/{comment_id}")
    def delete_comment(task_id: int, comment_id: int, conn=Depends(get_conn)):
        conn.execute("DELETE FROM task_activity WHERE id = ? AND task_id = ? AND kind = 'comment'", (comment_id, task_id))
        conn.commit()
        return {"ok": True}

    # ---- projects
    @r.post("/projects")
    def create_project(body: NewProject, conn=Depends(get_conn)):
        cur = conn.execute("INSERT INTO task_projects (name, color, description, archived, created_at) VALUES (?, ?, ?, 0, ?)",
                           (body.name.strip(), body.color, body.description.strip(), now_iso()))
        conn.commit()
        return dict(conn.execute("SELECT * FROM task_projects WHERE id = ?", (cur.lastrowid,)).fetchone())

    @r.patch("/projects/{project_id}")
    def update_project(project_id: int, body: ProjectPatch, conn=Depends(get_conn)):
        check_project(conn, project_id)
        sets = {k: (int(v) if k == "archived" else v) for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
        if sets:
            conn.execute(f"UPDATE task_projects SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", (*sets.values(), project_id))
            conn.commit()
        return dict(conn.execute("SELECT * FROM task_projects WHERE id = ?", (project_id,)).fetchone())

    @r.delete("/projects/{project_id}")
    def delete_project(project_id: int, conn=Depends(get_conn)):
        """Delete a project; its tasks stay, without a project."""
        check_project(conn, project_id)
        conn.execute("UPDATE tasks SET project_id = NULL WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM task_projects WHERE id = ?", (project_id,))
        conn.commit()
        return {"deleted": project_id}

    # ---- labels
    @r.delete("/labels/{name}")
    def delete_label(name: str, conn=Depends(get_conn)):
        """Delete a label and remove it from every task."""
        for t in conn.execute("SELECT id, labels FROM tasks WHERE labels LIKE ?", (f"%{name}%",)).fetchall():
            labels = [lb for lb in json.loads(t["labels"]) if lb != name]
            conn.execute("UPDATE tasks SET labels = ? WHERE id = ?", (json.dumps(labels, ensure_ascii=False), t["id"]))
        conn.execute("DELETE FROM task_labels WHERE name = ?", (name,))
        conn.commit()
        return {"deleted": name}

    return r
